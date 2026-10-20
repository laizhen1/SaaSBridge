"""Explicit embeddings also work with Chroma's Windows HTTP-only client."""
def embedding_function():
    from chromadb.utils.embedding_functions import ONNXMiniLM_L6_V2
    return ONNXMiniLM_L6_V2()


def require_local_chroma():
    """Give HTTP-only installations an actionable error before local fallback."""
    try:
        from chromadb.is_thin_client import is_thin_client
    except ImportError:
        is_thin_client = False
    if is_thin_client:
        raise RuntimeError(
            "ChromaDB is unavailable. The HTTP-only client requires its server. "
            "Run 'docker compose up -d' in D:\\SaaSBridge and check CHROMA_HOST/CHROMA_PORT."
        )
