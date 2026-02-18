from fastapi import APIRouter

router = APIRouter(prefix="/pyapi", tags=["test"])

@router.get("/")
def test():
    return "all functions loaded"