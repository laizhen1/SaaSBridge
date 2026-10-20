"""Convert OpenAPI 3.x JSON into the existing /knowledge/add payload.

No HTTP requests are made; external references are rejected. Local references
remain in the operation text, with referenced components emitted as documents.
"""
import argparse
import json
from pathlib import Path

METHODS = {"get", "post", "put", "patch", "delete", "head", "options", "trace"}


def openapi_documents(spec):
    if not isinstance(spec, dict) or not str(spec.get("openapi", "")).startswith("3."):
        raise ValueError("Expected an OpenAPI 3.x JSON object")
    if not isinstance(spec.get("paths"), dict):
        raise ValueError("OpenAPI paths must be an object")

    def validate_refs(value):
        if isinstance(value, dict):
            if "$ref" in value:
                ref = value["$ref"]
                if not isinstance(ref, str) or not ref.startswith("#/"):
                    raise ValueError("Only local OpenAPI references are supported")
                resolved = spec
                try:
                    for part in ref[2:].split("/"):
                        resolved = resolved[part.replace("~1", "/").replace("~0", "~")]
                except (KeyError, TypeError):
                    raise ValueError(f"Unresolved local reference: {ref}") from None
            for child in value.values():
                validate_refs(child)
        elif isinstance(value, list):
            for child in value:
                validate_refs(child)

    validate_refs(spec)
    info = spec.get("info", {})
    product = info.get("title", "API")
    version = info.get("version", "unknown")
    prefix = f"{product} {version}"
    docs = [{
        "title": f"{prefix} overview",
        "content": json.dumps({key: spec[key] for key in ("info", "servers", "security") if key in spec}, ensure_ascii=False),
    }]
    for path, item in spec["paths"].items():
        if not isinstance(item, dict):
            raise ValueError("Path items must be objects")
        if "$ref" in item:
            raise ValueError("Resolve path-item $ref entries before importing")
        for method, operation in item.items():
            if method.lower() not in METHODS:
                continue
            if not isinstance(operation, dict):
                raise ValueError("Operations must be objects")
            docs.append({
                "title": f"{prefix} {method.upper()} {path}",
                "content": json.dumps({
                    "method": method.upper(), "path": path,
                    "path_parameters": item.get("parameters", []),
                    "servers": item.get("servers", spec.get("servers", [])),
                    "security": operation.get("security", spec.get("security", [])),
                    "operation": operation,
                }, ensure_ascii=False),
            })
    for group, components in spec.get("components", {}).items():
        if not isinstance(components, dict):
            raise ValueError("Component groups must be objects")
        for key, value in components.items():
            docs.append({
                "title": f"{prefix} #/components/{group}/{key}",
                "content": json.dumps(value, ensure_ascii=False),
            })
    return docs


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("spec", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    docs = openapi_documents(json.loads(args.spec.read_text(encoding="utf-8-sig")))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({"documents": docs}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Prepared {len(docs)} documents in {args.output}")


if __name__ == "__main__":
    main()
