import { OAuthProvidersCard } from "@/components/OAuthProvidersCard";
import { Toast } from "@/components/Toast";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useToast } from "@/hooks/useToast";
import { api, type EnvVarInfo, type SetupStateResponse } from "@/lib/api";
import { PluginSlot } from "@/plugins";
import {
  ArrowRight,
  CheckCircle2,
  Circle,
  KeyRound,
  Loader2,
  Settings2,
  Sparkles,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";

const MODEL_PRESETS = [
  "anthropic/claude-sonnet-4.6",
  "openai/gpt-4.1",
  "google/gemini-2.5-pro",
  "deepseek/deepseek-reasoner",
];

const FALLBACK_PROVIDER_KEYS = [
  "OPENROUTER_API_KEY",
  "ANTHROPIC_API_KEY",
  "OPENAI_API_KEY",
  "NOUS_API_KEY",
];

function readModelValue(config: Record<string, unknown> | null): string {
  if (!config) return "";
  const modelValue = config.model;
  if (typeof modelValue === "string") return modelValue;
  if (
    modelValue &&
    typeof modelValue === "object" &&
    !Array.isArray(modelValue)
  ) {
    const defaultModel = (modelValue as Record<string, unknown>).default;
    if (typeof defaultModel === "string") return defaultModel;
  }
  return "";
}

export default function SetupPage() {
  const navigate = useNavigate();
  const { toast, showToast } = useToast();
  const [setupState, setSetupState] = useState<SetupStateResponse | null>(null);
  const [envVars, setEnvVars] = useState<Record<string, EnvVarInfo> | null>(
    null,
  );
  const [config, setConfig] = useState<Record<string, unknown> | null>(null);
  const [loading, setLoading] = useState(true);
  const [savingKey, setSavingKey] = useState(false);
  const [savingConfig, setSavingConfig] = useState(false);
  const [providerKey, setProviderKey] = useState("");
  const [providerValue, setProviderValue] = useState("");
  const [modelValue, setModelValue] = useState("");
  const [terminalBackend, setTerminalBackend] = useState("local");

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [state, vars, cfg] = await Promise.all([
        api.getSetupState(),
        api.getEnvVars(),
        api.getConfig(),
      ]);
      setSetupState(state);
      setEnvVars(vars);
      setConfig(cfg);
      setModelValue(state.model.value || readModelValue(cfg));
      setTerminalBackend(state.terminal.backend || "local");

      const preferredKeys = [
        ...state.provider.recommended_keys.map((k) => k.name),
        ...FALLBACK_PROVIDER_KEYS,
      ];
      const availableKeys = preferredKeys.filter((key) => vars[key] != null);
      const firstUnset = availableKeys.find((key) => !vars[key]?.is_set);
      const nextKey = firstUnset || availableKeys[0] || "";
      setProviderKey((prev) => (prev && vars[prev] ? prev : nextKey));
    } catch (error) {
      showToast(`Failed to load setup state: ${error}`, "error");
    } finally {
      setLoading(false);
    }
  }, [showToast]);

  useEffect(() => {
    void load();
  }, [load]);

  const providerOptions = useMemo(() => {
    if (!envVars || !setupState) return [];
    const keySet = new Set<string>();
    const options: Array<{
      key: string;
      description: string;
      isSet: boolean;
      url: string | null;
    }> = [];

    for (const item of setupState.provider.recommended_keys) {
      if (!envVars[item.name]) continue;
      keySet.add(item.name);
      options.push({
        key: item.name,
        description: item.description,
        isSet: envVars[item.name]?.is_set ?? item.is_set,
        url: item.url,
      });
    }

    for (const [key, info] of Object.entries(envVars)) {
      if (keySet.has(key)) continue;
      if (info.category !== "provider") continue;
      if (!(key.endsWith("_API_KEY") || key.endsWith("_TOKEN"))) continue;
      options.push({
        key,
        description: info.description,
        isSet: info.is_set,
        url: info.url,
      });
    }

    return options;
  }, [envVars, setupState]);

  const selectedProviderMeta = providerOptions.find(
    (o) => o.key === providerKey,
  );
  const checklist = setupState?.checklist;
  const ready = !!checklist?.provider && !!checklist?.model;
  const completeCount =
    Number(!!checklist?.provider) +
    Number(!!checklist?.model) +
    Number(!!checklist?.terminal);

  const saveProviderKey = async () => {
    if (!providerKey || !providerValue.trim()) return;
    setSavingKey(true);
    try {
      await api.setEnvVar(providerKey, providerValue.trim());
      setProviderValue("");
      showToast(`Saved ${providerKey}`, "success");
      window.dispatchEvent(new Event("hermes:setup-refresh"));
      await load();
    } catch (error) {
      showToast(`Failed to save ${providerKey}: ${error}`, "error");
    } finally {
      setSavingKey(false);
    }
  };

  const saveModelAndDefaults = async () => {
    if (!config) return;
    const trimmedModel = modelValue.trim();
    if (!trimmedModel) {
      showToast("Model is required.", "error");
      return;
    }

    const nextConfig = structuredClone(config);
    nextConfig.model = trimmedModel;
    const rawTerminal = nextConfig.terminal;
    const terminal =
      rawTerminal &&
      typeof rawTerminal === "object" &&
      !Array.isArray(rawTerminal)
        ? { ...(rawTerminal as Record<string, unknown>) }
        : {};
    terminal.backend = terminalBackend || "local";
    nextConfig.terminal = terminal;

    setSavingConfig(true);
    try {
      await api.saveConfig(nextConfig);
      setConfig(nextConfig);
      showToast("Saved model and runtime defaults.", "success");
      window.dispatchEvent(new Event("hermes:setup-refresh"));
      await load();
    } catch (error) {
      showToast(`Failed to save setup config: ${error}`, "error");
    } finally {
      setSavingConfig(false);
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center py-24">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
      </div>
    );
  }

  if (!setupState) {
    return (
      <div className="border border-destructive/30 bg-destructive/6 p-4 text-sm text-destructive">
        Setup state unavailable. Reload the dashboard.
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      <PluginSlot name="setup:top" />
      <Toast toast={toast} />

      <Card>
        <CardHeader>
          <div className="flex items-center gap-2">
            <Sparkles className="h-5 w-5 text-muted-foreground" />
            <CardTitle>Hermes GUI Setup</CardTitle>
          </div>
        </CardHeader>
        <CardContent className="grid gap-3 text-sm">
          <div className="flex items-center gap-2">
            <Badge variant={ready ? "success" : "outline"}>
              {ready ? "Ready" : "Setup Required"}
            </Badge>
            <span className="text-muted-foreground">
              {completeCount}/3 checks complete
            </span>
          </div>
          {setupState.is_fresh_mode && (
            <p className="text-xs text-success">
              Fresh mode active. This GUI run is isolated from your default
              install.
            </p>
          )}
          <p className="text-xs text-muted-foreground">
            Profile: <code>{setupState.profile}</code> · Home:{" "}
            <code>{setupState.hermes_home}</code>
          </p>
          <div className="grid gap-1 text-xs">
            <ChecklistItem
              done={setupState.checklist.provider}
              label="Provider credential connected"
            />
            <ChecklistItem
              done={setupState.checklist.model}
              label="Model selected"
            />
            <ChecklistItem
              done={setupState.checklist.terminal}
              label="Terminal backend configured"
            />
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <div className="flex items-center gap-2">
            <KeyRound className="h-5 w-5 text-muted-foreground" />
            <CardTitle>1) Connect a provider</CardTitle>
          </div>
        </CardHeader>
        <CardContent className="grid gap-4">
          <OAuthProvidersCard
            onError={(msg) => showToast(msg, "error")}
            onSuccess={(msg) => showToast(msg, "success")}
          />

          <div className="grid gap-2 border border-border p-3">
            <Label className="text-xs uppercase tracking-wide text-muted-foreground">
              API Key (manual)
            </Label>
            <div className="grid gap-2 sm:grid-cols-[minmax(0,1fr)_minmax(0,2fr)_auto]">
              <select
                value={providerKey}
                onChange={(e) => setProviderKey(e.target.value)}
                className="h-9 border border-border bg-background px-2 text-xs"
              >
                {providerOptions.map((option) => (
                  <option key={option.key} value={option.key}>
                    {option.key}
                    {option.isSet ? " (set)" : ""}
                  </option>
                ))}
              </select>
              <Input
                type="password"
                value={providerValue}
                onChange={(e) => setProviderValue(e.target.value)}
                placeholder="Paste API key"
                className="h-9 font-mono-ui text-xs"
              />
              <Button
                type="button"
                size="sm"
                className="h-9"
                onClick={saveProviderKey}
                disabled={savingKey || !providerKey || !providerValue.trim()}
              >
                {savingKey ? "Saving..." : "Save key"}
              </Button>
            </div>
            {selectedProviderMeta?.description && (
              <p className="text-xs text-muted-foreground">
                {selectedProviderMeta.description}
              </p>
            )}
            {selectedProviderMeta?.url && (
              <a
                href={selectedProviderMeta.url}
                target="_blank"
                rel="noreferrer"
                className="text-xs text-primary hover:underline"
              >
                Get key
              </a>
            )}
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <div className="flex items-center gap-2">
            <Settings2 className="h-5 w-5 text-muted-foreground" />
            <CardTitle>2) Choose model + runtime defaults</CardTitle>
          </div>
        </CardHeader>
        <CardContent className="grid gap-3">
          <div className="grid gap-2">
            <Label className="text-xs uppercase tracking-wide text-muted-foreground">
              Model
            </Label>
            <Input
              value={modelValue}
              onChange={(e) => setModelValue(e.target.value)}
              placeholder="anthropic/claude-sonnet-4.6"
              className="font-mono-ui text-xs"
            />
            <div className="flex flex-wrap gap-1">
              {MODEL_PRESETS.map((preset) => (
                <button
                  key={preset}
                  type="button"
                  onClick={() => setModelValue(preset)}
                  className="border border-border px-2 py-1 text-[11px] hover:bg-secondary/40"
                >
                  {preset}
                </button>
              ))}
            </div>
          </div>

          <div className="grid gap-2">
            <Label className="text-xs uppercase tracking-wide text-muted-foreground">
              Terminal backend
            </Label>
            <select
              value={terminalBackend}
              onChange={(e) => setTerminalBackend(e.target.value)}
              className="h-9 border border-border bg-background px-2 text-xs"
            >
              <option value="local">local</option>
              <option value="docker">docker</option>
              <option value="ssh">ssh</option>
              <option value="modal">modal</option>
              <option value="daytona">daytona</option>
              <option value="singularity">singularity</option>
            </select>
          </div>

          <Button
            type="button"
            onClick={saveModelAndDefaults}
            disabled={savingConfig || !modelValue.trim()}
            className="w-fit"
          >
            {savingConfig ? "Saving..." : "Save setup defaults"}
          </Button>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>3) Continue</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-wrap items-center gap-2">
          <Button
            type="button"
            onClick={() => navigate("/sessions", { replace: true })}
            disabled={!ready}
            className="gap-1.5"
          >
            Enter Hermes
            <ArrowRight className="h-3.5 w-3.5" />
          </Button>
          <Button
            type="button"
            variant="outline"
            onClick={() => navigate("/env")}
          >
            Advanced keys
          </Button>
          <Button
            type="button"
            variant="outline"
            onClick={() => navigate("/config")}
          >
            Advanced config
          </Button>
        </CardContent>
      </Card>
      <PluginSlot name="setup:bottom" />
    </div>
  );
}

function ChecklistItem({ done, label }: { done: boolean; label: string }) {
  return (
    <div className="flex items-center gap-2">
      {done ? (
        <CheckCircle2 className="h-3.5 w-3.5 text-success" />
      ) : (
        <Circle className="h-3.5 w-3.5 text-muted-foreground/60" />
      )}
      <span className="text-muted-foreground">{label}</span>
    </div>
  );
}
