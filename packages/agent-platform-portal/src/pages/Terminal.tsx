import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { Terminal as XTerm } from "@xterm/xterm";
import { AttachAddon } from "@xterm/addon-attach";
import { FitAddon } from "@xterm/addon-fit";
import "@xterm/xterm/css/xterm.css";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { api, ApiError } from "@/lib/api";

// Web terminal for a single VM. Fetches a short-lived signed ttyd WS
// URL from C1 (/api/me/instances/:vmId/ttyd-url) and attaches an
// xterm.js instance via AttachAddon. If the endpoint isn't up yet
// (M1 backend still in flight) we fall back to a local-echo "Demo
// mode" terminal so the UI still demos end to end (demo step 5).

type ConnState = "loading" | "connecting" | "connected" | "demo" | "closed" | "error";

function stateBadge(s: ConnState) {
  if (s === "connected") return <Badge variant="success">connected</Badge>;
  if (s === "connecting" || s === "loading") return <Badge variant="warning">{s}</Badge>;
  if (s === "demo") return <Badge variant="secondary">demo mode</Badge>;
  if (s === "closed") return <Badge variant="secondary">closed</Badge>;
  return <Badge variant="destructive">error</Badge>;
}

export default function Terminal() {
  const { vmId = "" } = useParams<{ vmId: string }>();
  const containerRef = useRef<HTMLDivElement | null>(null);
  const termRef = useRef<XTerm | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const fitRef = useRef<FitAddon | null>(null);
  const [state, setState] = useState<ConnState>("loading");
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);

  // Tear down WS + xterm. Called from cleanup and from the close button.
  const teardown = useCallback(() => {
    wsRef.current?.close();
    wsRef.current = null;
    termRef.current?.dispose();
    termRef.current = null;
    fitRef.current = null;
  }, []);

  useEffect(() => {
    if (!containerRef.current) return;
    let cancelled = false;

    const term = new XTerm({
      fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
      fontSize: 13,
      cursorBlink: true,
      convertEol: true,
    });
    const fit = new FitAddon();
    term.loadAddon(fit);
    term.open(containerRef.current);
    try {
      fit.fit();
    } catch {
      // jsdom / test envs don't have a real layout; ignore
    }
    termRef.current = term;
    fitRef.current = fit;

    const onResize = () => {
      try {
        fit.fit();
      } catch {
        // ignore — same reason as above
      }
    };
    window.addEventListener("resize", onResize);

    setState("loading");
    setError(null);

    api
      .getTtydUrl(vmId)
      .then((res) => {
        if (cancelled) return;
        setState("connecting");
        const ws = new WebSocket(res.url, ["tty"]);
        ws.binaryType = "arraybuffer";
        ws.onopen = () => {
          if (cancelled) return;
          setState("connected");
        };
        ws.onclose = () => {
          if (cancelled) return;
          setState("closed");
        };
        ws.onerror = () => {
          if (cancelled) return;
          setError("WebSocket error — see browser console");
          setState("error");
        };
        term.loadAddon(new AttachAddon(ws));
        wsRef.current = ws;
      })
      .catch((e: unknown) => {
        if (cancelled) return;
        // Demo-mode fallback: local echo terminal so the UI still
        // demos end to end while C1 ships the real endpoint.
        const msg = e instanceof ApiError ? e.message : String(e);
        setError(msg);
        setState("demo");
        term.writeln("\x1b[33m[demo mode]\x1b[0m C1 ttyd-url endpoint unavailable.");
        term.writeln("Type freely — input is echoed locally, nothing reaches a VM.");
        term.write("$ ");
        term.onData((data) => {
          // Translate CR to CRLF + repaint prompt so it feels shell-like.
          for (const ch of data) {
            if (ch === "\r") {
              term.write("\r\n$ ");
            } else if (ch === "\x7f") {
              term.write("\b \b");
            } else {
              term.write(ch);
            }
          }
        });
      });

    return () => {
      cancelled = true;
      window.removeEventListener("resize", onResize);
      teardown();
    };
    // attempt is included so the reconnect button forces a full re-run
  }, [vmId, attempt, teardown]);

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold">Terminal</h1>
          <p className="text-sm text-muted-foreground">
            VM <span className="font-mono">{vmId || "(none)"}</span>
          </p>
        </div>
        <div className="flex items-center gap-2">
          {stateBadge(state)}
          <Button size="sm" variant="outline" onClick={() => setAttempt((n) => n + 1)}>
            Reconnect
          </Button>
          <Button size="sm" variant="ghost" asChild>
            <Link to="/my-agents">Close</Link>
          </Button>
        </div>
      </div>

      {state === "demo" && (
        <Card>
          <CardContent className="p-4 text-sm">
            <div className="font-medium">Demo mode</div>
            <div className="text-xs text-muted-foreground">
              Could not fetch a ttyd WebSocket URL from the control plane.{" "}
              {error && <span className="font-mono">({error})</span>} The terminal below echoes
              input locally so the UI flow is still demoable. To wire the real ttyd backend in
              dev: <code>make ttyd-mock-up</code> + set{" "}
              <code>AGENT_PLATFORM_TTYD_MOCK_URL=ws://localhost:7681/ws</code> on C1 (W-3.1). The
              per-VM signing path lands with W-3.2 after PR-A + PR-F merge.
            </div>
          </CardContent>
        </Card>
      )}

      <div
        ref={containerRef}
        data-testid="terminal-container"
        className="h-[80vh] w-full rounded-md border bg-black p-2"
      />
    </div>
  );
}
