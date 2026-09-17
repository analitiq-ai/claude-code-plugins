"""Minimal valid documents the shape tests bend one field of.

A bend goes through `bent`, which copies first, so no case mutates these.
"""
from __future__ import annotations

import copy
from typing import Any, Callable

from analitiq.contracts.endpoint_identity import build_database_object

_H = "https://schemas.analitiq.ai"

API_CONNECTOR = {
    "$schema": f"{_H}/connector/latest.json",
    "kind": "api",
    "connector_id": "demo",
    "display_name": "Demo API",
    "version": "1.0.0",
    "auth": {"type": "none"},
    "connection_contract": {},
    "default_transport": "api",
    "transports": {
        "api": {"transport_type": "http", "base_url": "https://api.example.test"}
    },
}

DSN = {
    "kind": "url_template",
    "template": "postgresql://{host}/db",
    "bindings": {
        "host": {"value": {"ref": "connection.parameters.host"}, "encoding": "host"}
    },
}

DATABASE_CONNECTOR = {
    "$schema": f"{_H}/connector/latest.json",
    "kind": "database",
    "connector_id": "postgresql",
    "display_name": "PostgreSQL",
    "version": "1.0.0",
    "default_transport": "database",
    "transports": {
        "database": {
            "transport_type": "sqlalchemy",
            "driver": "postgresql+psycopg",
            "dsn": DSN,
        }
    },
    "auth": {"type": "db"},
    "connection_contract": {
        "inputs": {
            "host": {
                "source": "user",
                "phase": "pre_auth",
                "storage": "connection.parameters",
                "type": "string",
                "required": True,
            }
        }
    },
    "resource_discovery": {
        "strategy": "information_schema",
        "transport_ref": "database",
        "implementation": {"type": "builtin"},
        "produces": ["connection.endpoints", "connection.type_map"],
        "triggers": {
            "list_resources": "on_activation",
            "describe_resource": "on_resource_selected",
        },
    },
}

# The second SQL transport family, for the documents that key a bulk-load
# mechanism under `adbc`: RULE-CTOR-048 requires the connector to declare the
# transport whose bulk mechanism it names.
ADBC_TRANSPORT = {
    "transport_type": "adbc",
    "driver": "postgresql",
    "db_kwargs": {
        "uri": {"template": "postgresql://${connection.parameters.host}/db"}
    },
}

SQL_CAPABILITIES = {
    "catalog": "none",
    "session_targeting": "per_statement",
    "merge_form": "merge",
    "bulk_load": {"sqlalchemy": "copy_from"},
    "stage": {"scope": "temp", "schema": "target", "transactional_ddl": True},
}

PAGINATED_API_ENDPOINT = {
    "$schema": f"{_H}/api-endpoint/latest.json",
    "endpoint_id": "widgets",
    "operations": {
        "read": {
            "request": {
                "method": "GET",
                "path": "/v1/widgets",
                "query": {"offset": {"from_param": "offset"}},
            },
            "params": {
                "offset": {
                    "in": "query",
                    "type": "integer",
                    "required": False,
                    "controlled_by": "pagination",
                }
            },
            "pagination": {
                "type": "offset",
                "offset": {
                    "param": "offset",
                    "initial": 0,
                    "increment_by": {"ref": "response.record_count"},
                },
                "stop_when": {"empty": {"ref": "response.records"}},
            },
            "response": {
                "records": {"ref": "response.body.data"},
                "schema": {
                    "type": "object",
                    "properties": {
                        "data": {"type": "array", "items": {"type": "object"}}
                    },
                },
            },
        }
    },
}

DATABASE_ENDPOINT = {
    "$schema": f"{_H}/database-endpoint/latest.json",
    "endpoint_id": "orders",
    "database_object": {"schema": "public", "name": "orders"},
    "columns": [{"name": "id", "native_type": "TEXT", "arrow_type": "Utf8"}],
}

PIPELINE_ID = "11111111-1111-4111-8111-111111111111"
STREAM_ID = "44444444-4444-4444-8444-444444444444"

PIPELINE = {
    "$schema": f"{_H}/pipeline/latest.json",
    "pipeline_id": PIPELINE_ID,
    "display_name": "Demo to Postgres",
    "connections": {"source": "source_v1", "destinations": ["destination_v1"]},
    "streams": [STREAM_ID],
    "schedule": {"type": "manual", "timezone": "UTC"},
    "status": "draft",
}

DATABASE_OBJECT = build_database_object(None, "public", "orders")

STREAM = {
    "$schema": f"{_H}/stream/latest.json",
    "stream_id": STREAM_ID,
    "pipeline_id": PIPELINE_ID,
    "source": {
        "endpoint_ref": {
            "scope": "connector",
            "connection_id": "source_v1",
            "endpoint_id": "widgets",
        }
    },
    "destinations": [
        {
            "endpoint_ref": {
                "scope": "connection",
                "connection_id": "destination_v1",
                "database_object": DATABASE_OBJECT,
            },
            "write": {"mode": "insert"},
        }
    ],
    "mapping": {
        "assignments": [
            {
                "target": {"path": "city", "arrow_type": "Utf8"},
                "value": {
                    "kind": "expression",
                    "expression": {"op": "get", "path": ["city"]},
                },
            }
        ]
    },
}


def bent(document: dict[str, Any], bend: Callable[[dict[str, Any]], Any]) -> dict[str, Any]:
    """A deep copy of `document` with `bend` applied to it."""
    copied = copy.deepcopy(document)
    bend(copied)
    return copied
