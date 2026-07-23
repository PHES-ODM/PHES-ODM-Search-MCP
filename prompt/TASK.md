# MCP Client for PHES-ODM Parts Searching

I want to create a search MCP. The MCP is for running searches on the parts for
PHES-ODM using LLM embeddings. The embeddings should be made for all the parts
in the ODM, which includes all enumeration values as well as all classes and
slots specified in the LinkML schema. The user can provide a natural language
description and/or title of the term they want to find. The top-n results are
returned.

Each match should include:

- The match score
- The part ID, label, type, and description
- For matches that correspond to slots, report which class the slot belongs to
- For matches that correspond to enumeration values, report which enumeration
  the match belongs to, as well as which slots and classes the enumeration
  value can be entered under

## MCP Options

Flags can be provided to the MCP server by the client to limit what information
is returned by the request, to reduce how much data gets retrieved. Each of the
match results listed above should be able to be suppressed.

The caller should be able to search all possible fields mentioned in the match
results (ie. slots, classes, enumeration values, etc), or request that only
certain types of fields be searched.

## LLM Options

When creating the embeddings, the LLM used should be configurable, but by
default MiniLM can be used.

## Data Storage

Store the embeddings of the PHES-ODM to disk. The embeddings are stored on the
MCP server.

## Language

The MCP server should be written in Python.

## Files

There is one file that is used for creating the embeddings.

### odm_search_mcp/data/schemas/odm_v3.yaml

This file is a LinkML schema defining the PHES-ODM (version 3). It includes all
the classes, slots, an enumerations. These all correspond to different parts
defined in the parts file.

## Documentation

Create a complete README.md file to explain both the MCP client and server,
including how to set them up and some examples on how the client can make the
requests.
