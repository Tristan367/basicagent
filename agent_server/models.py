from pydantic import BaseModel


class ChatRequest(BaseModel):
    message: str
    # Paths to pictures the user attached. The message text already names them
    # so the agent's own tools can reach them; this list is what makes them
    # visible, and the client sends it because the client is the only thing
    # that knows which chips the user was looking at.
    images: list[str] = []
