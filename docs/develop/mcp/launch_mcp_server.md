---
sidebar_position: 1
slug: /launch_mcp_server
sidebar_custom_props: {
  categoryIcon: LucideTvMinimalPlay
}
---
# Launch RAGFlow MCP server

Launch an MCP server from source or via Docker.

---

A RAGFlow Model Context Protocol (MCP) server is designed as an independent component to complement the RAGFlow server. Note that an MCP server must operate alongside a properly functioning RAGFlow server. 

An MCP server can start up in either self-host mode (default) or host mode: 

- **Self-host mode**:  
  When launching an MCP server in self-host mode, you must provide an API key to authenticate the MCP server with the RAGFlow server. In this mode, the MCP server can access *only* the datasets of a specified tenant on the RAGFlow server.
- **Host mode**:  
  In host mode, each MCP client can access their own datasets on the RAGFlow server. However, each client request must include a valid API key to authenticate the client with the RAGFlow server.

Clients can connect through the legacy HTTP+SSE transport or the Streamable HTTP transport. Streamable HTTP returns JSON responses by default and can also return SSE responses.

## Prerequisites

1. Ensure RAGFlow is upgraded to v0.18.0 or later.
2. Have your RAGFlow API key ready. See [Acquire a RAGFlow API key](../acquire_ragflow_api_key.md).

:::tip INFO
If you wish to try out our MCP server without upgrading RAGFlow, community contributor [yiminghub2024](https://github.com/yiminghub2024) 👏 shares their recommended steps [here](#launch-an-mcp-server-without-upgrading-ragflow).
:::

## Launch an MCP server 

You can start an MCP server either from source code or via Docker. 

### Launch from source code

1. Ensure that a RAGFlow server v0.18.0+ is properly running.
2. Launch the MCP server:


```bash
# Launch the MCP server to work in self-host mode, run either of the following
uv run mcp/server/server.py --host=127.0.0.1 --port=9382 --base-url=http://127.0.0.1:9380 --api-key=ragflow-xxxxx
# uv run mcp/server/server.py --host=127.0.0.1 --port=9382 --base-url=http://127.0.0.1:9380 --mode=self-host --api-key=ragflow-xxxxx

# To launch the MCP server to work in host mode, run the following instead:
# uv run mcp/server/server.py --host=127.0.0.1 --port=9382 --base-url=http://127.0.0.1:9380 --mode=host
```

Where: 

- `host`: The MCP server's host address.
- `port`: The MCP server's listening port.
- `base_url`: The address of the running RAGFlow server.
- `mode`: The launch mode.
  - `self-host`: (default) self-host mode.
  - `host`: host mode.
- `api_key`: Required in self-host mode to authenticate the MCP server with the RAGFlow server. See [here](../acquire_ragflow_api_key.md) for instructions on acquiring an API key.

### Transports

The RAGFlow MCP server supports two transports in both self-host and host modes: the legacy SSE transport (served at `/sse`), introduced on November 5, 2024, and deprecated on March 26, 2025, and the Streamable HTTP transport (served at `/mcp`). Both transports are enabled by default, and Streamable HTTP returns JSON responses by default. To disable either transport, use `--no-transport-sse-enabled` or `--no-transport-streamable-http-enabled`. To use SSE responses over Streamable HTTP instead of JSON responses, use `--no-json-response`.

For deployments that use only Streamable HTTP, pass `--no-transport-sse-enabled` or set `RAGFLOW_MCP_TRANSPORT_SSE_ENABLED=false`. This disables both legacy routes, `/sse` and `/messages/`, while keeping `/mcp` available. Restart the MCP process after changing its transport configuration. Clients using the legacy routes must switch to `/mcp` with a Streamable HTTP client.

When running from source, the server loads `.env` from its script directory or the nearest parent directory. A local `mcp/server/.env` can contain `RAGFLOW_MCP_TRANSPORT_SSE_ENABLED=false`. Environment values override command-line transport flags; an existing process environment value takes precedence over `.env`. Keep this local deployment file outside version control.

### Cumulative tool call counts

The server saves one cumulative integer per tool in
`logs/mcp-tool-counts-<port>.json` under the project directory. For example,
port 9396 uses `logs/mcp-tool-counts-9396.json`. Override the file location with
`RAGFLOW_MCP_TOOL_COUNTS_FILE`; use a persistent mounted directory in Docker
and include this file in backups. Do not delete it during log cleanup.

Each known tool call that enters the execution handler adds one, including
calls that subsequently fail. Initialization, tool discovery, notifications,
unknown tools, and requests rejected before execution do not count. Only tool
names and counts are stored, without arguments, identities, timestamps, or
durations. These are lifetime totals, not daily history, and start at zero when
the feature is enabled; earlier HTTP access logs cannot supply historical totals.

The request handler increments memory only. A background task writes changed
counts every 10 seconds using an atomic replacement; a graceful shutdown also
flushes pending counts. Restarting reloads the saved totals. A process crash or
power loss can lose increments since the last successful save; failed writes
are logged and retried without blocking tool execution. Invalid existing data
causes startup to fail rather than silently resetting the counts.

Use one file per server process. A file lock prevents simultaneous writers to
the same file (Unix hosts). Read the latest persisted totals with:

```bash
python -m json.tool logs/mcp-tool-counts-9396.json
```

### Launch from Docker

#### 1. Enable MCP server

The MCP server is designed as an optional component that complements the RAGFlow server and disabled by default. To enable MCP server:

1. Navigate to **docker/docker-compose.yml**.
2. Add the MCP flags to the existing `command` of the active `ragflow-cpu` or `ragflow-gpu` service, as shown below for `ragflow-cpu`:

```yaml {6-13}
  services:
    ragflow-cpu:
      ...
      image: ${RAGFLOW_IMAGE}
      # Example configuration to set up an MCP server:
      command:
        - --enable-adminserver
        - --init-model-provider-tables
        - --enable-mcpserver
        - --mcp-host=0.0.0.0
        - --mcp-port=9382
        - --mcp-base-url=http://127.0.0.1:9380
        - --mcp-script-path=/ragflow/mcp/server/server.py
        - --mcp-mode=self-host
        - --mcp-host-api-key=ragflow-xxxxxxx
        # Optional transport flags for the RAGFlow MCP server.
        # In host mode, each MCP request must include the client's RAGFlow API key.
        # The legacy SSE transport and the streamable-HTTP transport with JSON responses are enabled by default.
        # To disable a specific transport or JSON responses for the streamable-HTTP transport, use the corresponding flag(s):
        #   - --no-transport-sse-enabled # Disables the legacy SSE endpoint (/sse)
        #   - --no-transport-streamable-http-enabled #  Disables the streamable-HTTP transport (served at the /mcp endpoint)
        #   - --no-json-response # Disables JSON responses for the streamable-HTTP transport
```

Where: 

- `mcp-host`: The MCP server's host address.
- `mcp-port`: The MCP server's listening port.
- `mcp-base-url`: The address of the running RAGFlow server.
- `mcp-script-path`: The file path to the MCP server’s main script.
- `mcp-mode`: The launch mode.
  - `self-host`: (default) self-host mode.
  - `host`: host mode.
- `mcp-host-api_key`: Required in self-host mode to authenticate the MCP server with the RAGFlow server. See [here](../acquire_ragflow_api_key.md) for instructions on acquiring an API key.

:::tip INFO
Host mode supports Streamable HTTP at `/mcp`. Set `--mcp-mode=host` and omit `--mcp-host-api-key`; each client must send its own RAGFlow API key in the `Authorization: Bearer <API key>` header on every request, including initialization, tool discovery, and tool calls. For access through a non-local hostname, configure `RAGFLOW_MCP_ALLOWED_HOSTS` to include that hostname. If browser clients send an `Origin` header, configure `RAGFLOW_MCP_ALLOWED_ORIGINS` for their origins as well.
:::

#### 2. Launch a RAGFlow server with an MCP server

Run `docker compose -f docker-compose.yml up` to launch the RAGFlow server together with the MCP server.

*The following ASCII art confirms a successful launch:*

```bash
  docker-ragflow-cpu-1  | Starting MCP Server on 0.0.0.0:9382 with base URL http://127.0.0.1:9380...
  docker-ragflow-cpu-1  | Starting 1 task executor(s) on host 'dd0b5e07e76f'...
  docker-ragflow-cpu-1  | 2025-04-18 15:41:18,816 INFO     27 ragflow_server log path: /ragflow/logs/ragflow_server.log, log levels: {'peewee': 'WARNING', 'pdfminer': 'WARNING', 'root': 'INFO'}
  docker-ragflow-cpu-1  | 
  docker-ragflow-cpu-1  | __  __  ____ ____       ____  _____ ______     _______ ____
  docker-ragflow-cpu-1  | |  \/  |/ ___|  _ \     / ___|| ____|  _ \ \   / / ____|  _ \
  docker-ragflow-cpu-1  | | |\/| | |   | |_) |    \___ \|  _| | |_) \ \ / /|  _| | |_) |
  docker-ragflow-cpu-1  | | |  | | |___|  __/      ___) | |___|  _ < \ V / | |___|  _ <
  docker-ragflow-cpu-1  | |_|  |_|\____|_|        |____/|_____|_| \_\ \_/  |_____|_| \_\
  docker-ragflow-cpu-1  |     
  docker-ragflow-cpu-1  | MCP launch mode: self-host
  docker-ragflow-cpu-1  | MCP host: 0.0.0.0
  docker-ragflow-cpu-1  | MCP port: 9382
  docker-ragflow-cpu-1  | MCP base_url: http://127.0.0.1:9380
  docker-ragflow-cpu-1  | INFO:     Started server process [26]
  docker-ragflow-cpu-1  | INFO:     Waiting for application startup.
  docker-ragflow-cpu-1  | INFO:     Application startup complete.
  docker-ragflow-cpu-1  | INFO:     Uvicorn running on http://0.0.0.0:9382 (Press CTRL+C to quit)
  docker-ragflow-cpu-1  | 2025-04-18 15:41:20,469 INFO     27 found 0 gpus
  docker-ragflow-cpu-1  | 2025-04-18 15:41:23,263 INFO     27 init database on cluster mode successfully
  docker-ragflow-cpu-1  | 2025-04-18 15:41:25,318 INFO     27 load_model /ragflow/rag/res/deepdoc/det.onnx uses CPU
  docker-ragflow-cpu-1  | 2025-04-18 15:41:25,367 INFO     27 load_model /ragflow/rag/res/deepdoc/rec.onnx uses CPU
  docker-ragflow-cpu-1  |         ____   ___    ______ ______ __               
  docker-ragflow-cpu-1  |        / __ \ /   |  / ____// ____// /____  _      __
  docker-ragflow-cpu-1  |       / /_/ // /| | / / __ / /_   / // __ \| | /| / /
  docker-ragflow-cpu-1  |      / _, _// ___ |/ /_/ // __/  / // /_/ /| |/ |/ / 
  docker-ragflow-cpu-1  |     /_/ |_|/_/  |_|\____//_/    /_/ \____/ |__/|__/                             
  docker-ragflow-cpu-1  | 
  docker-ragflow-cpu-1  |     
  docker-ragflow-cpu-1  | 2025-04-18 15:41:29,088 INFO     27 RAGFlow version: v0.18.0-285-gb2c299fa full
  docker-ragflow-cpu-1  | 2025-04-18 15:41:29,088 INFO     27 project base: /ragflow
  docker-ragflow-cpu-1  | 2025-04-18 15:41:29,088 INFO     27 Current configs, from /ragflow/conf/service_conf.yaml:
  docker-ragflow-cpu-1  |  ragflow: {'host': '0.0.0.0', 'http_port': 9380}
  ...
  docker-ragflow-cpu-1  |  * Running on all addresses (0.0.0.0)
  docker-ragflow-cpu-1  |  * Running on http://127.0.0.1:9380
  docker-ragflow-cpu-1  |  * Running on http://172.19.0.6:9380
  docker-ragflow-cpu-1  |   ______           __      ______                     __            
  docker-ragflow-cpu-1  |  /_  __/___ ______/ /__   / ____/  _____  _______  __/ /_____  _____
  docker-ragflow-cpu-1  |   / / / __ `/ ___/ //_/  / __/ | |/_/ _ \/ ___/ / / / __/ __ \/ ___/
  docker-ragflow-cpu-1  |  / / / /_/ (__  ) ,<    / /____>  </  __/ /__/ /_/ / /_/ /_/ / /    
  docker-ragflow-cpu-1  | /_/  \__,_/____/_/|_|  /_____/_/|_|\___/\___/\__,_/\__/\____/_/                               
  docker-ragflow-cpu-1  |     
  docker-ragflow-cpu-1  | 2025-04-18 15:41:34,501 INFO     32 TaskExecutor: RAGFlow version: v0.18.0-285-gb2c299fa full
  docker-ragflow-cpu-1  | 2025-04-18 15:41:34,501 INFO     32 Use Elasticsearch http://es01:9200 as the doc engine.
  ...
```

#### Launch an MCP server without upgrading RAGFlow

:::info KUDOS
This section is contributed by our community contributor [yiminghub2024](https://github.com/yiminghub2024). 👏
:::

1. Prepare all MCP-specific files and directories.  
   i. Copy the [mcp/](https://github.com/infiniflow/ragflow/tree/main/mcp) directory to your local working directory.  
   ii. Copy [docker/docker-compose.yml](https://github.com/infiniflow/ragflow/blob/main/docker/docker-compose.yml) locally.  
   iii. Copy [docker/entrypoint.sh](https://github.com/infiniflow/ragflow/blob/main/docker/entrypoint.sh) locally.  
   iv. Install the required dependencies using `uv`:  
       - Run `uv add mcp` or
       - Copy [pyproject.toml](https://github.com/infiniflow/ragflow/blob/main/pyproject.toml) locally and run `uv sync --python 3.13`.
2. Edit **docker-compose.yml** to enable MCP (disabled by default).
3. Launch the MCP server:

```bash
docker compose -f docker-compose.yml up -d
```

### Check MCP server status

Run the following to check the logs the RAGFlow server and the MCP server:

```bash
docker logs docker-ragflow-cpu-1
```

## Security considerations

As MCP technology is still at early stage and no official best practices for authentication or authorization have been established, RAGFlow currently uses [API key](../acquire_ragflow_api_key.md) to validate identity for the operations described earlier. However, in public environments, this makeshift solution could expose your MCP server to potential network attacks. Therefore, when running a local SSE server, it is recommended to bind only to localhost (`127.0.0.1`) rather than to all interfaces (`0.0.0.0`). 

For further guidance, see the [official MCP documentation](https://modelcontextprotocol.io/docs/concepts/transports#security-considerations).

## Frequently asked questions

### When to use an API key for authentication?

The use of an API key depends on the operating mode of your MCP server. 

- **Self-host mode** (default):  
  When starting the MCP server in self-host mode, you should provide an API key when launching it to authenticate it with the RAGFlow server:  
  - If launching from source, include the API key in the command. 
  - If launching from Docker, update the API key in **docker/docker-compose.yml**.
- **Host mode**:  
  If your RAGFlow MCP server is working in host mode, include the API key in the `headers` of your client requests to authenticate your client with the RAGFlow server. An example is available [here](https://github.com/infiniflow/ragflow/blob/main/mcp/client/client.py).
