# Runtime Flows

## 1. Bootstrap Flow

```text
user starts backend/bootstrap.py
  -> Hivemind DHT starts with stable identity_path
  -> bootstrap multiaddress is printed
  -> address is copied into backend/constants.py
  -> serving nodes and clients use it as initial_peers
```

The bootstrap node is only a discovery entry point. It does not serve model layers.

## 2. Start Serving Node Flow

Frontend:

```text
Network page
  -> Serve Layers tab
  -> select model
  -> choose layer_start/layer_end
  -> choose device
  -> POST /node/start
```

Backend:

```text
/node/start
  -> validate model and token
  -> Node(...)
  -> node.start()
      -> start DHT
      -> load requested layers
      -> start RPC server
      -> announce metadata to DHT
      -> start periodic announce loop
```

DHT writes:

```text
{prefix}.node_info.{peer_id}
{prefix}.members
```

## 3. Stop Serving Node Flow

```text
Network page
  -> STOP NODE
  -> POST /node/stop
  -> node.stop()
      -> stop RPC server
      -> unload layers
      -> shutdown DHT
```

Current limitation: the node does not remove itself from `{prefix}.members`. The metadata has an expiry, but the members list can keep stale peer ids.

## 4. Start Generator Flow

Frontend:

```text
Network page
  -> Run Inference tab
  -> select model
  -> set bootstrap peers
  -> POST /generator/start
```

Backend:

```text
/generator/start
  -> validate model and token
  -> create client DHT
  -> create RemoteSequential
  -> create DistributedGenerator
  -> load tokenizer, embeddings, final norm, lm_head
  -> return ready
```

Current limitation: generator startup does not verify that the DHT has a usable route for the selected model. The first real inference request may be where route failures appear.

## 5. WebSocket Inference Flow

Frontend:

```text
Inference page
  -> user sends text
  -> create WebSocket if needed
  -> send JSON message to /stream
```

Backend:

```text
/stream receives message
  -> validate message and generation params
  -> if generator loaded:
       async for chunk in generator.generate_stream(...)
           send token/error/done chunk to frontend
     else:
       send mock response
```

Generator:

```text
tokenize prompt
for each new token:
  embed generated ids
  create attention mask and position ids
  RemoteSequential.forward(...)
    -> discover nodes
    -> check coverage
    -> sort by layer_start
    -> call each remote node
  apply final norm
  apply lm_head
  sample next token
  yield decoded token
```

Remote node:

```text
Hivemind RPC receives hidden states
  -> _HandlerModule.forward(...)
  -> InferenceHandler.forward(...)
  -> move tensors to node device/dtype
  -> run local layer range
  -> return hidden states to client
```

## 6. Dashboard Polling Flow

Dashboard polls:

- `/stats` every 2 seconds
- `/nodes` every 5 seconds
- `/status` every 5 seconds

`/nodes` uses the active local node DHT if available, otherwise the generator DHT.

Current limitation: `/nodes` always uses `DHT_PREFIX` from constants, not a dynamic prefix selected in the UI.

## 7. Settings Flow

```text
Settings page
  -> GET /settings
  -> POST /settings/token
  -> DELETE /settings/token
```

The backend stores the token in `backend/.hf_token` with file permission `0600`.

## 8. Failure Flow Today

Many failures are discovered late:

- no DHT peers
- no nodes announced
- incomplete layer coverage
- bad `rpc_uid`
- remote node offline
- model architecture mismatch
- attention mask shape or dtype mismatch

Most of these should become explicit readiness checks before enabling inference.

