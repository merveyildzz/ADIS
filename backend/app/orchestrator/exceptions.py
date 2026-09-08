"""All upload-validation failures share this base so a route handler can
catch one type and return a clean 4xx — never a raw parser/library traceback."""


class UploadValidationError(Exception):
    """Base class for every reason an uploaded file can be rejected."""


class EmptyFileError(UploadValidationError):
    pass


class FileTooLargeError(UploadValidationError):
    pass


class InvalidFileTypeError(UploadValidationError):
    pass


class EncodingDetectionError(UploadValidationError):
    pass


class CSVParseError(UploadValidationError):
    pass


class NoColumnsError(UploadValidationError):
    pass
