"""
scos_tools.py — SCOS Tool Registry

Every tool Selyrion can invoke is registered here with a contract.
All invocations are logged to claudecode.db execution_traces.
"""
import sqlite3, hashlib, time, json
from pathlib import Path
from typing import Callable
from trace_writer import Trace

CLAUDECODE_DB = Path.home() / "claudecode.db"


class Tool:
    def __init__(self, name, description, input_schema, fn):
        self.name         = name
        self.description  = description
        self.input_schema = input_schema
        self.fn           = fn

    def invoke(self, inputs):
        missing = [k for k, v in self.input_schema.items()
                   if v.get("required") and k not in inputs]
        if missing:
            return {"status": "error", "error": f"Missing required inputs: {missing}"}
        return self.fn(inputs)


class ToolRegistry:
    def __init__(self):
        self._tools = {}

    def register(self, tool):
        self._tools[tool.name] = tool

    def invoke(self, name, inputs, session_id="scos.session", domain=""):
        if name not in self._tools:
            return {"status": "error", "error": f"Unknown tool: {name}"}
        tool = self._tools[name]
        outcome = "success"
        result  = {}
        with Trace(name, session_id, domain=domain,
                   intent=f"{name}: {str(inputs)[:120]}") as trace:
            result  = tool.invoke(inputs)
            outcome = "failure" if result.get("status") == "error" else "success"
            trace.set_output(result)
            if outcome == "success":
                trace.succeed()
            else:
                trace.fail(result.get("error", ""))
        return result

    def list_tools(self):
        return [{"name": t.name, "description": t.description,
                 "inputs": t.input_schema}
                for t in self._tools.values()]

    def describe(self):
        lines = ["Available SCOS tools:"]
        for t in self._tools.values():
            req = [k for k, v in t.input_schema.items() if v.get("required")]
            lines.append(f"  {t.name}: {t.description} (required: {req})")
        return "\n".join(lines)


registry = ToolRegistry()

def register_tool(name, description, input_schema):
    def decorator(fn):
        registry.register(Tool(name, description, input_schema, fn))
        return fn
    return decorator
