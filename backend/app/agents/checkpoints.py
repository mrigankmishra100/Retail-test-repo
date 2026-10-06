"""Bounded synchronous LangGraph checkpoints on the application's JSON cache adapter."""
from base64 import b64encode, b64decode
from hashlib import sha256
from threading import RLock
import json
from langgraph.checkpoint.base import BaseCheckpointSaver, CheckpointTuple, WRITES_IDX_MAP
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from app.oci.cache import CacheUnavailable


class CacheCheckpointer(BaseCheckpointSaver):
    """One bounded graph history per thread; callers must hold workflow_lock while writing."""
    def __init__(self, cache, *, namespace="retail"):
        super().__init__(serde=JsonPlusSerializer(pickle_fallback=False,
            allowed_json_modules=[], allowed_msgpack_modules=[("langgraph.types", "Interrupt")]))
        self.cache, self._lock = cache, RLock()
        if namespace not in {"retail", "supplier"}:
            raise ValueError("Unknown workflow namespace")
        self.namespace = namespace

    def _key(self, config):
        values = config["configurable"]
        if values.get("checkpoint_ns", ""):
            raise ValueError("Subgraph checkpoint namespaces are not supported")
        return self.namespace + "_checkpoint:" + sha256(values["thread_id"].encode()).hexdigest()

    def _pack(self, value):
        kind, data = self.serde.dumps_typed(value)
        return [kind, b64encode(data).decode("ascii")]

    def _unpack(self, value):
        if value[0] not in {"msgpack", "json", "bytes", "null"}:
            raise CacheUnavailable("Invalid checkpoint encoding")
        return self.serde.loads_typed((value[0], b64decode(value[1], validate=True)))

    def _load(self, config):
        data = self.cache.get_state(self._key(config))
        if data is None:
            return {"format": 1, "records": {}}
        if data.get("format") != 1 or not isinstance(data.get("records"), dict):
            raise CacheUnavailable("Invalid checkpoint format")
        return data

    def _save(self, config, data):
        # Public workflow API exposes no time travel. Retain recent replay/pending-write history only.
        for key in sorted(data["records"])[:-40]:
            del data["records"][key]
        if len(json.dumps(data).encode()) > 4 * 1024 * 1024:
            raise CacheUnavailable("Workflow checkpoint size limit reached")
        if not self.cache.set_state(self._key(config), data):
            raise CacheUnavailable("Checkpoint was not stored")

    def get_tuple(self, config):
        with self._lock:
            records = self._load(config)["records"]
            key = config["configurable"].get("checkpoint_id") or max(records, default=None)
            record = records.get(key)
            if record is None:
                return None
            base = {"thread_id": config["configurable"]["thread_id"], "checkpoint_ns": ""}
            return CheckpointTuple(config={"configurable": base | {"checkpoint_id": key}},
                checkpoint=self._unpack(record["checkpoint"]), metadata=self._unpack(record["metadata"]),
                parent_config={"configurable": base | {"checkpoint_id": record["parent"]}} if record["parent"] else None,
                pending_writes=[(v[0], v[1], self._unpack(v[2])) for v in record["writes"].values()])

    def put(self, config, checkpoint, metadata, new_versions):
        with self._lock:
            data = self._load(config)
            data["records"][checkpoint["id"]] = {"checkpoint": self._pack(checkpoint),
                "metadata": self._pack(metadata), "parent": config["configurable"].get("checkpoint_id"), "writes": {}}
            self._save(config, data)
        return {"configurable": {"thread_id": config["configurable"]["thread_id"],
            "checkpoint_ns": "", "checkpoint_id": checkpoint["id"]}}

    def put_writes(self, config, writes, task_id, task_path=""):
        with self._lock:
            data = self._load(config)
            record = data["records"].get(config["configurable"]["checkpoint_id"])
            if record is None:
                raise CacheUnavailable("Checkpoint expired before pending write")
            for index, (channel, value) in enumerate(writes):
                idx = WRITES_IDX_MAP.get(channel, index)
                key = f"{task_id}:{idx}"
                if idx >= 0 and key in record["writes"]:
                    continue
                # Do not persist raw dependency exceptions (which may contain credentials).
                record["writes"][key] = [task_id, channel, self._pack("node_failed" if channel == "__error__" else value)]
            self._save(config, data)
