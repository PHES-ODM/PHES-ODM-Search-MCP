# PHES-ODM Embedding Search — MCP Server

An MCP (Model Context Protocol) server that lets any MCP-compatible client search PHES-ODM parts (classes, slots, and enumeration values) using natural language queries backed by sentence-transformer embeddings.

---

## Contents

```
PHES-ODM-Search-MCP/
├── odm_search_mcp/
│   ├── __init__.py
│   ├── server.py          # FastMCP server (entry point)
│   ├── embedder.py        # Embedding creation, persistence, and search
│   ├── schema_parser.py   # Parses the LinkML YAML schema into an index
│   └── data/
│       └── schemas/
│           └── odm_v3.yaml   # LinkML schema for PHES-ODM v3
├── prompt/
│   └── TASK.md            # Original project specification
├── Dockerfile             # Docker image definition
├── docker-compose.yml     # Docker Compose service configuration
├── nginx.conf             # nginx reverse proxy configuration
├── pyproject.toml         # Package build configuration
├── requirements.txt
├── README.md
├── DOCKER.md              # Deployment guide (Docker + AWS EC2)
└── SERVER.md              # Deployment guide (nginx + systemd on Debian)
```

Embeddings are stored under `embeddings/` (created automatically on first run).

---

## Deployment

| Guide                           | Description                                                                               |
|---------------------------------|-------------------------------------------------------------------------------------------|
| [DOCKER.md](DOCKER.md)          | Run the server as a Docker container; deploy to AWS EC2 with nginx and Let's Encrypt TLS  |
| [SERVER.md](SERVER.md)          | Deploy directly on Debian Linux with nginx and systemd (no Docker)                        |

---

## Setup

### 1. Install dependencies

```bash
pip install -e .
```

This installs the package in editable mode along with all its dependencies,
making `odm_search_mcp` importable from any working directory — which is
required for the Claude Desktop integration.

The default embedding model is `all-MiniLM-L6-v2` (downloaded automatically by `sentence-transformers` on first use, ~90 MB).

### 2. Start the server

```bash
python -m odm_search_mcp.server
```

On the **first run** the server parses the schema, encodes all ~2 300 parts, and
saves the resulting vectors to `embeddings/`.  Subsequent starts load the cached
vectors and are much faster.

#### CLI options

| Flag | Env var | Default | Description |
|------|---------|---------|-------------|
| `--schema PATH` | `ODM_SCHEMA` | `odm_search_mcp/data/schemas/odm_v3.yaml` | LinkML schema file |
| `--store DIR` | `ODM_STORE` | `embeddings/` | Directory for cached embeddings |
| `--model NAME` | `ODM_MODEL` | `all-MiniLM-L6-v2` | Sentence-transformers model |
| `--rebuild` | — | false | Rebuild the embeddings index and exit (does not start the server) |
| `--transport` | — | `stdio` | MCP transport: `stdio`, `http` (streamable HTTP), or `sse` |

Use `--rebuild` to pre-build (or re-build) the embeddings index without
starting the server.  The process exits automatically once the index is
written to disk, so no Ctrl-C is needed.  Run this whenever you update
the schema or switch embedding models.

**Example — pre-build the index:**

```bash
python -m odm_search_mcp.server --rebuild
```

**Example — use a different model and rebuild the index:**

```bash
python -m odm_search_mcp.server --model all-mpnet-base-v2 --rebuild
```

**Example — run over streamable HTTP transport:**

```bash
python -m odm_search_mcp.server --transport http
```

**Example — run over SSE transport:**

```bash
python -m odm_search_mcp.server --transport sse
```

### 3. Register with Claude Desktop (stdio)

Add an entry to `claude_desktop_config.json` on your local machine. The file
location depends on the operating system:

| OS | Path |
|----|------|
| macOS | `~/Library/Application Support/Claude/claude_desktop_config.json` |
| Windows | `%APPDATA%\Claude\claude_desktop_config.json` |
| Linux | `~/.config/Claude/claude_desktop_config.json` |

```json
{
  "mcpServers": {
    "phes-odm-search": {
      "command": "/absolute/path/to/python3",
      "args": ["-m", "odm_search_mcp.server"],
      "cwd": "/absolute/path/to/PHES-ODM-Search-MCP",
      "env": {}
    }
  }
}
```

To get the path to your `python3` executable under `"command"`, be sure you are
in the virtual environment you used to run the `pip install` command and run:

```console
which python3
```

### 4. Register with Claude Code (stdio)

There are two ways to register the server with Claude Code.

#### Option A — CLI (recommended)

```bash
claude mcp add phes-odm-search \
  --transport stdio \
  --env ODM_STORE=/absolute/path/to/PHES-ODM-Search-MCP/embeddings \
  -- /absolute/path/to/python3 -m odm_search_mcp.server
```

Claude Code does not pass a working directory to the spawned server process,
so set `ODM_STORE` to an absolute path to ensure embeddings are always written
to the same location.

By default the entry is stored under local scope (`~/.claude.json`) and is not
shared. Add `--scope project` to write to `.mcp.json` in the current directory
(committed to version control and shared with the team), or `--scope user` to
make it available across all your projects.

#### Option B — edit `.mcp.json` directly (project scope)

Create `.mcp.json` in the project root:

```json
{
  "mcpServers": {
    "phes-odm-search": {
      "type": "stdio",
      "command": "/absolute/path/to/python3",
      "args": ["-m", "odm_search_mcp.server"],
      "env": {
        "ODM_STORE": "/absolute/path/to/PHES-ODM-Search-MCP/embeddings"
      }
    }
  }
}
```

To get the `python3` path, activate the virtual environment you used for `pip install`
and run `which python3`.

---

## Tools

### `search_odm_parts`

Search for PHES-ODM parts using a natural language query.

**Input parameters**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `query` | string | *(required)* | Natural language description or title of the term to find |
| `top_n` | integer | `10` | Maximum number of results |
| `part_types` | string[] \| null | `null` | Filter by schema type: `"class"`, `"slot"`, `"enum"`, `"enum_value"` |
| `include_score` | boolean | `true` | Include cosine similarity score |
| `include_id` | boolean | `true` | Include part ID |
| `include_label` | boolean | `true` | Include human-readable label |
| `include_type` | boolean | `true` | Include schema type |
| `include_description` | boolean | `true` | Include part description |
| `include_class_info` | boolean | `true` | For slot matches: include the list of classes the slot belongs to |
| `include_range` | boolean | `true` | For slot matches: include the list of allowed range types (e.g. `"string"`, an enum name, a class name); multiple values when `any_of` is used |
| `include_required` | boolean | `true` | For slot matches: include the list of classes in which this slot is marked required; empty list means not required anywhere |
| `include_enum_info` | boolean | `true` | For enum_value matches: include the parent enumeration name, the slots that accept it, and the classes those slots belong to |

**Output**

A JSON array of match objects, ordered by descending similarity score.
Fields present depend on the `include_*` flags and the `type` of the
matched part.

**`slot` match** — includes slot-specific fields when the corresponding
`include_*` flags are `true`:

```json
[
  {
    "score": 0.8341,
    "id": "collDT",
    "label": "Collection Date and Time",
    "type": "slot",
    "description": "The date and time at which the sample was collected.",
    "belongs_to_classes": ["samples"],
    "slot_ranges": ["dateTime"],
    "required_by_classes": ["samples"]
  }
]
```

**`enum_value` match** — includes enum-value-specific fields when
`include_enum_info` is `true`:

```json
[
  {
    "score": 0.7921,
    "id": "grab",
    "label": "Grab",
    "type": "enum_value",
    "description": "A sample collected at a single point in time.",
    "belongs_to_enum": "samplingTypeSet",
    "used_by_slots": ["sampType"],
    "used_by_classes": ["samples"]
  }
]
```

**`class` or `enum` match** — only the base fields are present:

```json
[
  {
    "score": 0.7503,
    "id": "samples",
    "label": "Samples",
    "type": "class",
    "description": "A table containing wastewater sample collection records."
  }
]
```

---

### `get_enum_values`

Return all permissible values for a named enumeration.

**Input parameters**

| Parameter   | Type   | Default       | Description                         |
| ----------- | ------ | ------------- | ----------------------------------- |
| `enum_name` | string | *(required)*  | The enumeration's schema identifier |

**Output**

A JSON array of value objects, in schema order. Each object has:

| Field         | Description                                        |
| ------------- | -------------------------------------------------- |
| `value`       | Short identifier / code for this permissible value |
| `title`       | Human-readable name                                |
| `description` | Prose explanation of the value                     |

```json
[
  {
    "value": "gm3",
    "title": "Gram per cubic metre",
    "description": "Density unit."
  },
  {
    "value": "hUn",
    "title": "See Header for Unit",
    "description": "Indicates that unit info is in the column header."
  }
]
```

An error is returned if `enum_name` is not found in the schema.

---

### `get_class_slots`

Return all slots belonging to a named class, with their full schema-level details.

**Input parameters**

| Parameter    | Type   | Default      | Description                        |
| ------------ | ------ | ------------ | ---------------------------------- |
| `class_name` | string | *(required)* | Schema identifier for the class,   |
|              |        |              | e.g. `"wideNames"`, `"Sample"`     |

**Output**

A JSON array of slot objects, ordered as they appear in the class definition.
Each object has:

| Field         | Description                                              |
| ------------- | -------------------------------------------------------- |
| `part_id`     | Slot identifier (partID)                                 |
| `title`       | Human-readable name from `slot_usage`                    |
| `description` | Prose description of the slot in this class context      |
| `range`       | List of allowed types; multiple entries when `any_of`    |
|               | is used (e.g. `["string", "genMissingnessSet"]`)         |
| `pattern`     | Regex pattern constraint, or empty string if none        |
| `identifier`  | `true` if this slot is the primary key for this class    |
| `required`    | `true` if this slot is required in this class            |

```json
[
  {
    "part_id": "wideName",
    "title": "Wide Name",
    "description": "Unique identifier for wide table only.",
    "range": ["string"],
    "pattern": "^.{0,30}$",
    "identifier": true,
    "required": true
  },
  {
    "part_id": "descr",
    "title": "Description",
    "description": "A detailed description of a measure, method, or attribute.",
    "range": ["string", "genMissingnessSet"],
    "pattern": "^.{0,1000}$",
    "identifier": false,
    "required": true
  }
]
```

An error is returned if `class_name` is not found in the schema.

---

### `list_part_types`

Returns the list of all distinct `schema_type` values present in the loaded index.  Useful to discover valid values for the `part_types` filter.

**Output example**

```json
["class", "enum", "enum_value", "slot"]
```

---

## Example queries

### Find where to record viral load concentration

```json
{
  "query": "viral concentration in wastewater",
  "top_n": 5
}
```

### Search only among classes

```json
{
  "query": "laboratory measurement table",
  "top_n": 3,
  "part_types": ["class"]
}
```

### Find enumeration values for sample collection type — minimal response

```json
{
  "query": "grab composite sample type",
  "top_n": 5,
  "part_types": ["enum_value"],
  "include_description": false,
  "include_class_info": false
}
```

### Find slot for pH measurement, suppress enum details

```json
{
  "query": "pH of wastewater sample",
  "top_n": 5,
  "part_types": ["slot"],
  "include_enum_info": false
}
```

---

## Schema types

| `schema_type` | Meaning |
|---------------|---------|
| `class` | A data table / entity class in the LinkML schema (e.g. `samples`, `measurements`) |
| `slot` | A field / column defined in the schema (e.g. `collDT`, `value`) |
| `enum` | A named enumeration (controlled vocabulary set) |
| `enum_value` | A permissible value within an enumeration (e.g. `grab`, `composite`) |

All parts are sourced directly from the LinkML schema; every class, slot,
enumeration, and permissible value present in the schema is indexed.

---

## Changing the embedding model

Any model accepted by `sentence-transformers` can be used.  After changing the model, rebuild the index:

```bash
python -m odm_search_mcp.server --model paraphrase-multilingual-MiniLM-L12-v2 --rebuild
```

Popular alternatives:

| Model | Size | Notes |
|-------|------|-------|
| `all-MiniLM-L6-v2` | ~90 MB | Default — fast, good quality |
| `all-mpnet-base-v2` | ~420 MB | Higher quality, slower |
| `paraphrase-multilingual-MiniLM-L12-v2` | ~470 MB | Multilingual support |
