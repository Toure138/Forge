from pydantic import BaseModel


class ErrorBody(BaseModel):
    """Error response of every API error (except FastAPI's 422 validation format).

    `code` is stable and machine-readable (the frontend translates it, FR-47); `message` is a
    short generic English text that never carries internal details.
    """

    code: str
    message: str
