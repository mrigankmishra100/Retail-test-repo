from dataclasses import dataclass

from app.distributor_rag.core.config import Settings
from app.distributor_rag.interfaces.ingestion import DocumentIngestionProvider
from app.distributor_rag.interfaces.llm import LLMProvider
from app.distributor_rag.interfaces.storage import ObjectStorageProvider
from app.distributor_rag.interfaces.vector_search import VectorSearchProvider


@dataclass
class Providers:
    llm: LLMProvider
    storage: ObjectStorageProvider
    vector: VectorSearchProvider
    ingestion: DocumentIngestionProvider


def create_providers(settings: Settings) -> Providers:
    settings.validate_provider()
    if settings.cloud_provider == "oci":
        from app.distributor_rag.providers.oci.factory import create_oci_providers

        return create_oci_providers()
    if settings.cloud_provider == "aws":
        import boto3

        from app.distributor_rag.providers.aws.bedrock_llm import AWSBedrockLLMProvider
        from app.distributor_rag.providers.aws.common import client
        from app.distributor_rag.providers.aws.ingestion import AWSKnowledgeBaseIngestionProvider
        from app.distributor_rag.providers.aws.knowledge_base import AWSKnowledgeBaseVectorProvider
        from app.distributor_rag.providers.aws.s3_storage import AWSS3StorageProvider

        session = boto3.Session(profile_name=settings.aws_profile or None)
        s3 = client("s3", settings.aws_region, session)
        return Providers(
            AWSBedrockLLMProvider(
                settings.aws_region,
                settings.aws_bedrock_model_id,
                client("bedrock-runtime", settings.aws_region, session),
            ),
            AWSS3StorageProvider(settings.aws_region, settings.aws_s3_bucket, settings.aws_s3_prefix, s3),
            AWSKnowledgeBaseVectorProvider(
                settings.aws_region,
                settings.aws_knowledge_base_id,
                client("bedrock-agent-runtime", settings.aws_region, session),
                mode=settings.aws_kb_mode,
            ),
            AWSKnowledgeBaseIngestionProvider(
                settings.aws_region,
                settings.aws_knowledge_base_id,
                settings.aws_kb_data_source_id,
                settings.aws_s3_bucket,
                settings.aws_s3_prefix,
                agent=client("bedrock-agent", settings.aws_region, session),
                s3=s3,
                mode=settings.aws_kb_mode,
            ),
        )
    raise ValueError("Unsupported provider")
