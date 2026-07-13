"""Child-process callables used by adapter integration tests."""

from __future__ import annotations

import os
import random
import time

from gauntlet.adapters import JsonObject, ToolCallError, ToolRegistry

_CALLS = 0


def tool_agent(payload: JsonObject, *, tools: ToolRegistry) -> JsonObject:
    key = payload.get("key")
    return {"result": tools.call("lookup", {"key": key})}


def recovery_agent(payload: JsonObject, *, tools: ToolRegistry) -> JsonObject:
    key = payload.get("key")
    try:
        tools.call("lookup", {"key": key})
    except ToolCallError:
        return {"result": tools.call("lookup", {"key": key}), "recovered": True}
    return {"recovered": False}


def echo_environment(payload: JsonObject, *, tools: ToolRegistry) -> JsonObject:
    del tools
    print("target diagnostic", flush=True)
    return {
        "payload": payload,
        "secret": os.environ.get("GAUNTLET_TEST_SECRET"),
        "api_key": os.environ.get("OPENAI_API_KEY"),
        "https_proxy": os.environ.get("HTTPS_PROXY"),
        "pythonpath": os.environ.get("PYTHONPATH"),
        "path": os.environ.get("PATH"),
        "home": os.environ.get("HOME"),
        "hash_seed": os.environ.get("PYTHONHASHSEED"),
    }


def conditional_hang(payload: JsonObject, *, tools: ToolRegistry) -> JsonObject:
    del tools
    if payload.get("hang") is True:
        time.sleep(30)
    return {"completed": True}


def stateful(payload: JsonObject, *, tools: ToolRegistry) -> JsonObject:
    del payload, tools
    global _CALLS
    _CALLS += 1
    return {"calls": _CALLS}


def determinism_probe(payload: JsonObject, *, tools: ToolRegistry) -> JsonObject:
    del payload, tools
    return {"hash": hash("gauntlet"), "random": random.random()}


def corrupt_protocol_stdout(payload: JsonObject, *, tools: ToolRegistry) -> JsonObject:
    del payload, tools
    os.write(1, b"not-json\n")
    return {"completed": True}


def wrong_signature(payload: JsonObject) -> JsonObject:
    return payload


def raises(payload: JsonObject, *, tools: ToolRegistry) -> JsonObject:
    del payload, tools
    raise RuntimeError("target exploded")


def non_object(payload: JsonObject, *, tools: ToolRegistry) -> object:
    del payload, tools
    return ["not", "an", "object"]
