"""Knowledge-base domain errors safe to translate into JSON responses."""


class KnowledgeBaseError(Exception):
    pass


class KnowledgeValidationError(KnowledgeBaseError):
    pass


class UnsupportedDocumentType(KnowledgeValidationError):
    pass


class DocumentNotFound(KnowledgeBaseError):
    pass


class DuplicateDocumentError(KnowledgeBaseError):
    def __init__(self, document_id: str) -> None:
        self.document_id = document_id
        super().__init__(f"duplicate document; existing document_id={document_id}")


class RequiresOCRError(KnowledgeBaseError):
    pass


class EmbeddingProviderUnavailable(KnowledgeBaseError):
    pass


class VectorStoreUnavailable(KnowledgeBaseError):
    pass


class IndexCompatibilityError(KnowledgeBaseError):
    pass


class KnowledgeAccessDenied(KnowledgeBaseError):
    pass


class DeletionConsistencyError(KnowledgeBaseError):
    pass
