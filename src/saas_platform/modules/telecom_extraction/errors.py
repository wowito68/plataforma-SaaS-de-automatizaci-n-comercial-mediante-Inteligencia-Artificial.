from saas_platform.errors import PermanentError, TransientError, ValidationError


class ExtractionValidationError(ValidationError):
    code = "extraction_validation_error"


class ExtractionInvalidResponseError(PermanentError):
    code = "extraction_invalid_response"


class ExtractionTimeoutError(TransientError):
    code = "extraction_timeout"


class ExtractionRateLimitError(TransientError):
    code = "extraction_rate_limit"


class ExtractionTemporaryProviderError(TransientError):
    code = "extraction_provider_temporary"


class ExtractionAuthenticationError(PermanentError):
    code = "extraction_authentication"


class ExtractionPermanentProviderError(PermanentError):
    code = "extraction_provider_permanent"


class ExtractionRefusedError(PermanentError):
    code = "extraction_refused"


class ExtractionEmptyResponseError(PermanentError):
    code = "extraction_empty_response"


class ExtractionIncompleteResponseError(TransientError):
    code = "extraction_incomplete_response"
