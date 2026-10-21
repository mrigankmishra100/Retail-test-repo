import math
import oci
from app.distributor_rag.domain.errors import ProviderError
from app.distributor_rag.providers.oci.common import oci_operation, serving

class OCIEmbeddings:
    def __init__(self, client, settings):
        self.client, self.settings = client, settings

    def embed(self, texts, input_type):
        vectors = []
        settings = self.settings
        with oci_operation("embedding"):
            for offset in range(0, len(texts), 16):
                batch = texts[offset:offset + 16]
                kwargs = dict(inputs=batch, compartment_id=settings.oci_compartment_id,
                    serving_mode=serving(settings.oci_embedding_model_id, settings.oci_embedding_serving_mode),
                    input_type=input_type, truncate="NONE")
                # Only request configurable output dimensions when the model supports that option.
                if settings.oci_embedding_set_dimensions:
                    kwargs["output_dimensions"] = settings.oci_embedding_dimensions
                result = self.client.embed_text(oci.generative_ai_inference.models.EmbedTextDetails(**kwargs)).data
                output = result.embeddings
                if len(output) != len(batch):
                    raise ProviderError("Embedding response count did not match the input.")
                for vector in output:
                    if len(vector) != settings.oci_embedding_dimensions or not all(math.isfinite(x) for x in vector) or not any(vector):
                        raise ProviderError("Embedding dimensions or numeric values do not match the configured index.")
                vectors.extend(output)
        return vectors
