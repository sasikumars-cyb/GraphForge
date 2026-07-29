import { useEffect, useMemo, useRef, useState } from "react";
import { Play, Pause, RotateCcw } from "lucide-react";
import type { AgentStep, WorkflowStageInfo } from "../../types/agent";
import {
  buildWorkflowTimeline,
  formatDuration,
  type TimelineEvent,
} from "../../lib/workflowDerived";

interface WorkflowReplayPanelProps {
  stages: WorkflowStageInfo[];
  stepsByRunId: Map<string, AgentStep>;
}

const SPEEDS = [1, 5, 20, 60] as const;
const TICK_MS = 100;

const KIND_STYLE: Record<string, string> = {
  lifecycle: "text-fg-muted",
  tool_call: "text-info-fg",
  graph_traversal: "text-cat-7-fg",
  graph_fact: "text-success-fg",
  llm_reasoning: "text-warning-fg",
};

/** "Hackathon wow factor" feature — Workflow Replay. Every event here is
 * one already rendered elsewhere (ExecutionLogPanel / AgentActivityFeed);
 * this merges them into one chronological, scrubbable timeline across all
 * four stages via buildWorkflowTimeline(), so a completed run can be
 * watched back like a flight recorder. No new endpoint, no new agent, no
 * fabricated events — pure client-side playback over data already fetched
 * for the page. */
export function WorkflowReplayPanel({ stages, stepsByRunId }: WorkflowReplayPanelProps) {
  const timeline = useMemo(
    () => buildWorkflowTimeline(stages, stepsByRunId),
    [stages, stepsByRunId],
  );
  const startMs = timeline[0]?.atMs ?? 0;
  const endMs = timeline[timeline.length - 1]?.atMs ?? 0;

  const [playheadMs, setPlayheadMs] = useState(startMs);
  const [isPlaying, setIsPlaying] = useState(false);
  const [speed, setSpeed] = useState<(typeof SPEEDS)[number]>(20);
  const feedRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    setPlayheadMs(startMs);
  }, [startMs]);

  useEffect(() => {
    if (!isPlaying) return;
    const id = window.setInterval(() => {
      setPlayheadMs((prev) => {
        const next = prev + TICK_MS * speed;
        if (next >= endMs) {
          setIsPlaying(false);
          return endMs;
        }
        return next;
      });
    }, TICK_MS);
    return () => window.clearInterval(id);
  }, [isPlaying, speed, endMs]);

  const revealed = timeline.filter((e) => e.atMs <= playheadMs);
  const current: TimelineEvent | undefined = revealed[revealed.length - 1];

  useEffect(() => {
    const el = feedRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [revealed.length]);

  if (timeline.length === 0) {
    return (
      <p className="text-sm text-fg-muted">
        Replay unlocks once at least one stage has finished — there's nothing to play back yet.
      </p>
    );
  }

  const totalSpan = Math.max(1, endMs - startMs);
  const progressPct = ((playheadMs - startMs) / totalSpan) * 100;
  const atEnd = playheadMs >= endMs;

  const handlePlayPause = () => {
    if (atEnd) {
      setPlayheadMs(startMs);
      setIsPlaying(true);
      return;
    }
    setIsPlaying((p) => !p);
  };

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-3">
        <button
          type="button"
          onClick={handlePlayPause}
          className="inline-flex items-center gap-2 rounded-lg bg-accent-solid px-3 py-1.5 text-sm font-medium text-accent-on-solid transition-colors hover:brightness-110"
          aria-label={isPlaying ? "Pause replay" : atEnd ? "Restart replay" : "Play replay"}
        >
          {isPlaying ? (
            <Pause className="h-4 w-4" aria-hidden="true" />
          ) : (
            <Play className="h-4 w-4" aria-hidden="true" />
          )}
          {isPlaying ? "Pause" : atEnd ? "Replay" : "Play"}
        </button>

        <button
          type="button"
          onClick={() => {
            setIsPlaying(false);
            setPlayheadMs(startMs);
          }}
          className="inline-flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-xs font-medium text-fg-muted ring-1 ring-inset ring-line transition-colors hover:bg-surface-raised hover:text-fg-secondary"
          aria-label="Restart from the beginning"
        >
          <RotateCcw className="h-3.5 w-3.5" aria-hidden="true" />
          Restart
        </button>

        <div className="flex items-center gap-1" role="group" aria-label="Playback speed">
          {SPEEDS.map((s) => (
            <button
              key={s}
              type="button"
              onClick={() => setSpeed(s)}
              aria-pressed={speed === s}
              className={`rounded-md px-2 py-1 text-xs font-medium transition-colors ${
                speed === s
                  ? "bg-accent-bg text-accent-fg ring-1 ring-inset ring-accent-line/40"
                  : "text-fg-muted hover:bg-surface-raised hover:text-fg-secondary"
              }`}
            >
              {s}x
            </button>
          ))}
        </div>

        <span className="ml-auto font-mono text-xs tabular-nums text-fg-muted">
          {formatDuration(Math.max(0, playheadMs - startMs))} / {formatDuration(endMs - startMs)}
        </span>
      </div>

      <div className="flex flex-col gap-1.5">
        <input
          type="range"
          min={startMs}
          max={endMs}
          step={TICK_MS}
          value={playheadMs}
          onChange={(e) => {
            setIsPlaying(false);
            setPlayheadMs(Number(e.target.value));
          }}
          aria-label="Replay position"
          aria-valuetext={`${formatDuration(playheadMs - startMs)} of ${formatDuration(endMs - startMs)}`}
          className="h-1.5 w-full cursor-pointer appearance-none rounded-full accent-accent-solid"
          style={{
            background: `linear-gradient(to right, var(--color-brand-500) ${progressPct}%, var(--color-slate-800) ${progressPct}%)`,
          }}
        />
        <div className="relative h-3">
          {stages
            .filter((s) => s.run_id && stepsByRunId.has(s.run_id))
            .map((s) => {
              const first = timeline.find((e) => e.stage === s.stage);
              if (!first) return null;
              const pct = ((first.atMs - startMs) / totalSpan) * 100;
              return (
                <span
                  key={s.stage}
                  style={{ left: `${pct}%` }}
                  className="absolute -translate-x-1/2 text-[10px] font-medium text-fg-subtle"
                >
                  {s.label}
                </span>
              );
            })}
        </div>
      </div>

      <div
        className="rounded-lg bg-canvas px-3 py-2 text-sm font-medium text-fg-secondary"
        aria-hidden="true"
      >
        {current ? (
          <>
            <span className="text-accent-fg">{current.agentLabel}</span>
            <span className="text-fg-subtle"> — </span>
            <span className={KIND_STYLE[current.kind] ?? "text-fg-secondary"}>{current.text}</span>
          </>
        ) : (
          <span className="text-fg-subtle">Press play to begin the replay.</span>
        )}
      </div>
      <p className="sr-only" aria-live="polite">
        {current ? `${current.agentLabel}: ${current.text}` : ""}
      </p>

      <div
        ref={feedRef}
        className="flex max-h-72 flex-col gap-0.5 overflow-y-auto rounded-lg bg-canvas p-3 font-mono text-[11.5px] leading-relaxed"
      >
        {revealed.map((ev, i) => {
          const showStageDivider = i === 0 || revealed[i - 1].stage !== ev.stage;
          return (
            <div key={ev.key}>
              {showStageDivider && (
                <p className="mt-2 mb-1 text-[10px] font-semibold tracking-wide text-fg-subtle uppercase first:mt-0">
                  {ev.agentLabel}
                </p>
              )}
              <div className="grid grid-cols-[52px_1fr] gap-3 py-0.5 animate-[activity-in_200ms_ease-out_backwards]">
                <span className="text-fg-subtle">
                  +{formatDuration(Math.max(0, ev.atMs - startMs))}
                </span>
                <span className={KIND_STYLE[ev.kind] ?? "text-fg-secondary"}>{ev.text}</span>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
