"""Local OpenAPI 3.0/3.1 JSON or YAML ingestion; never fetch remote references."""
import argparse
import hashlib
import json
from pathlib import Path
from urllib.parse import unquote

import yaml

METHODS = {"get", "post", "put", "patch", "delete", "head", "options", "trace"}
MAX_IMPORT_BYTES = 10 * 1024 * 1024


def load_structured_text(text, suffix=".json"):
    if len(text.encode("utf-8")) > MAX_IMPORT_BYTES:
        raise ValueError("Document exceeds 10 MB")
    try:
        if suffix.lower() in {".yaml", ".yml"}:
            # Aliases can create cyclic graphs or huge expansions.
            for event in yaml.parse(text, Loader=yaml.SafeLoader):
                if isinstance(event, yaml.AliasEvent):
                    raise ValueError("YAML aliases are not supported; expand them before importing")
            return yaml.safe_load(text)
        return json.loads(text.lstrip("\ufeff"))
    except (yaml.YAMLError, json.JSONDecodeError, RecursionError) as ex:
        raise ValueError("Invalid JSON/YAML document") from ex


def openapi_documents(spec):
    if not isinstance(spec, dict) or not str(spec.get("openapi", "")).startswith(("3.0.", "3.1.")):
        raise ValueError("Expected an OpenAPI 3.0/3.1 object")
    if not isinstance(spec.get("paths"), dict):
        raise ValueError("OpenAPI paths must be an object")
    info = spec.get("info")
    if not isinstance(info, dict) or not all(isinstance(info.get(key), str) and info[key].strip() for key in ("title", "version")):
        raise ValueError("OpenAPI info requires a title and string version")

    def resolve(ref):
        if not isinstance(ref, str) or not ref.startswith("#/"):
            raise ValueError("Only local OpenAPI references are supported")
        resolved = spec
        try:
            for part in unquote(ref[2:]).split("/"):
                key = part.replace("~1", "/").replace("~0", "~")
                resolved = resolved[int(key)] if isinstance(resolved, list) else resolved[key]
        except (KeyError, TypeError, IndexError, ValueError):
            raise ValueError("Unresolved local reference") from None
        return resolved

    count = 0

    def validate(value, depth=0):
        nonlocal count
        count += 1
        if count > 100000 or depth > 60:
            raise ValueError("OpenAPI document is too complex")
        if isinstance(value, dict):
            if not all(isinstance(key, str) for key in value):
                raise ValueError("OpenAPI object keys must be strings (quote YAML response codes)")
            if "$ref" in value:
                resolve(value["$ref"])
            for child in value.values():
                validate(child, depth + 1)
        elif isinstance(value, list):
            for child in value:
                validate(child, depth + 1)
        elif value is not None and not isinstance(value, (str, int, float, bool)):
            raise ValueError("OpenAPI contains a non-JSON value; quote YAML dates")

    validate(spec)
    product, version = info["title"], info["version"]
    prefix = f"{product} {version}"
    namespace = hashlib.sha256(json.dumps([product, version], ensure_ascii=False).encode()).hexdigest()[:24]

    def document(key, title, content):
        return {"document_id": f"openapi:{namespace}:{key}", "title": f"{prefix} {title}",
                "content": json.dumps(content, ensure_ascii=False, indent=2, allow_nan=False),
                "doc_type": "openapi", "source": f"OpenAPI: {product}", "product_version": version}

    docs = [document("overview", "overview", {key: spec[key] for key in ("info", "servers", "security", "tags") if key in spec})]
    for path, item in spec["paths"].items():
        if not path.startswith("/") or not isinstance(item, dict):
            raise ValueError("Path items must be objects with paths starting with /")
        seen = set()
        while "$ref" in item:
            ref = item["$ref"]
            if ref in seen:
                raise ValueError("Circular path-item reference")
            seen.add(ref)
            resolved = resolve(ref)
            if not isinstance(resolved, dict):
                raise ValueError("Path-item reference must point to an object")
            item = {**resolved, **{key: value for key, value in item.items() if key != "$ref"}}
        for method, operation in item.items():
            if method not in METHODS:
                continue
            if not isinstance(operation, dict):
                raise ValueError("Operations must be objects")
            docs.append(document(f"{method}:{path}", f"{method.upper()} {path}", {
                "method": method.upper(), "path": path,
                "path_parameters": item.get("parameters", []),
                "servers": operation.get("servers", item.get("servers", spec.get("servers", []))),
                "security": operation.get("security", spec.get("security", [])),
                "operation": operation,
            }))
    components = spec.get("components", {})
    if not isinstance(components, dict):
        raise ValueError("Components must be an object")
    for group, values in components.items():
        if group.startswith("x-"):
            continue
        if not isinstance(values, dict):
            raise ValueError("Component groups must be objects")
        for key, value in values.items():
            pointer = "#/components/" + group + "/" + key.replace("~", "~0").replace("/", "~1")
            docs.append(document(pointer, pointer, value))
    return docs


def knowledge_documents(text, filename):
    """Legacy arrays and /knowledge/add envelopes remain supported."""
    suffix = Path(filename).suffix.lower()
    if suffix in {".txt", ".md"}:
        return [{"title": Path(filename).stem, "content": text}]
    if suffix not in {".json", ".yaml", ".yml"}:
        raise ValueError("Only .txt, .md, .json, .yaml and .yml files are supported")
    data = load_structured_text(text, suffix)
    if isinstance(data, dict) and "openapi" in data:
        return openapi_documents(data)
    if suffix in {".yaml", ".yml"}:
        raise ValueError("YAML uploads must be OpenAPI 3.0/3.1 documents")
    if isinstance(data, dict):
        data = data.get("documents")
    if not isinstance(data, list) or not data:
        raise ValueError("Expected a nonempty document array, documents envelope, or OpenAPI spec")
    return data


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("spec", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    if args.spec.stat().st_size > MAX_IMPORT_BYTES:
        parser.error("Document exceeds 10 MB")
    docs = openapi_documents(load_structured_text(args.spec.read_text(encoding="utf-8-sig"), args.spec.suffix))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({"documents": docs}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Prepared {len(docs)} documents in {args.output}")


if __name__ == "__main__":
    main()
