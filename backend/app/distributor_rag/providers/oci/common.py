from contextlib import contextmanager
import hashlib
import json
import oci
from app.distributor_rag.core.config import ROOT
from app.distributor_rag.domain.errors import ProviderError

@contextmanager
def oci_operation(operation):
    try:
        yield
    except ProviderError:
        raise
    except Exception as exc:
        raise ProviderError("OCI " + operation + " failed. Check configuration, access and service availability.") from exc


def load_identity(settings):
    from pathlib import Path
    path = Path(settings.oci_config_file).expanduser()
    if not path.is_absolute():
        path = ROOT / path
    config = oci.config.from_file(str(path), settings.oci_profile)
    # Downloaded credential bundles use a key path relative to the project root.
    if config.get("key_file") and not Path(config["key_file"]).expanduser().is_absolute():
        config["key_file"] = str(ROOT / config["key_file"])
    config["region"] = settings.oci_region
    return config


def serving(model_id, mode):
    models = oci.generative_ai_inference.models
    return models.DedicatedServingMode(endpoint_id=model_id) if mode == "dedicated" else models.OnDemandServingMode(model_id=model_id)


def index_identity(settings):
    data = [settings.oci_embedding_model_id, settings.oci_embedding_endpoint,
            settings.oci_embedding_serving_mode, settings.oci_embedding_dimensions,
            settings.oci_chunk_chars, settings.oci_chunk_overlap, "native-text-v1"]
    # Preserve identities of existing native indexes, never adopt them as JSON.
    if getattr(settings, 'oci_vector_storage', 'native') == 'json':
        data.append('bounded-json-cosine-v1')
    return hashlib.sha256(json.dumps(data).encode()).hexdigest()
