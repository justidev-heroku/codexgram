# Стаб MCP-сервера telegram для смоук-теста codex-gram (копия прототипа).
# Логирует каждый tools/call в файл LOG (argv[1]) и отвечает "sent".
import sys, json
LOG = sys.argv[1]
def send(o): sys.stdout.write(json.dumps(o)+"\n"); sys.stdout.flush()
for line in sys.stdin:
    m = json.loads(line); mid = m.get("id"); meth = m.get("method")
    if meth == "initialize":
        send({"jsonrpc":"2.0","id":mid,"result":{"protocolVersion":m["params"].get("protocolVersion","2025-06-18"),"capabilities":{"tools":{}},"serverInfo":{"name":"telegram","version":"0"}}})
    elif meth == "tools/list":
        send({"jsonrpc":"2.0","id":mid,"result":{"tools":[{"name":"reply","description":"Send text reply to Telegram","inputSchema":{"type":"object","properties":{"chat_id":{"type":"string"},"text":{"type":"string"}},"required":["chat_id","text"]}}]}})
    elif meth == "tools/call":
        open(LOG,"a").write(json.dumps(m["params"])+"\n")
        send({"jsonrpc":"2.0","id":mid,"result":{"content":[{"type":"text","text":"sent (id: 1)"}]}})
    elif mid is not None:
        send({"jsonrpc":"2.0","id":mid,"result":{}})
