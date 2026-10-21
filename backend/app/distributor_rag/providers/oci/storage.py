import oci
from app.distributor_rag.domain.models import StoredObject
from app.distributor_rag.providers.oci.common import oci_operation

class OCIStorageProvider:
    def __init__(self, client, settings):
        self.client, self.settings = client, settings
        self.namespace, self.bucket = settings.oci_namespace, settings.oci_bucket

    def upload_document(self, document, content):
        with oci_operation("object upload"):
            self.client.put_object(self.namespace, self.bucket, document.storage_key, content,
                content_type=document.content_type, opc_meta={"document_id": document.id, "owner_id": document.owner_id})
        return StoredObject(key=document.storage_key, reference="oci:" + self.namespace + "/" + self.bucket + "/" + document.storage_key)

    def get_document(self, key):
        with oci_operation("object read"):
            response = self.client.get_object(self.namespace, self.bucket, key)
            try:
                return response.data.content
            finally:
                response.data.close()

    def delete_document(self, key):
        with oci_operation("object deletion"):
            try:
                self.client.delete_object(self.namespace, self.bucket, key)
            except oci.exceptions.ServiceError as exc:
                if exc.status != 404:
                    raise

    def list_documents(self):
        items, start = [], None
        with oci_operation("object listing"):
            while True:
                kwargs = {"prefix": self.settings.object_storage_prefix}
                if start:
                    kwargs["start"] = start
                page = self.client.list_objects(self.namespace, self.bucket, **kwargs).data
                items.extend(StoredObject(key=obj.name, reference="oci:" + self.namespace + "/" + self.bucket + "/" + obj.name) for obj in page.objects)
                start = page.next_start_with
                if not start:
                    break
        return items
