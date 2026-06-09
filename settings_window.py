"""Settings window for the meeting workflow app — tabbed configuration UI.

All settings persist to ~/.meeting_workflow/config.json via the config module.
Live components register via config.on_change() to react without restart.
"""

from __future__ import annotations

import copy
import tkinter as tk
from tkinter import messagebox, ttk

import config as cfg_mod
import knowledge_base as kb

CLAUDE_MODELS = [
    "claude-opus-4-7",
    "claude-opus-4-6",
    "claude-sonnet-4-6",
    "claude-haiku-4-5",
]
EFFORTS = ["low", "medium", "high", "max"]
OPENAI_EMBED_MODELS = [
    "text-embedding-3-small",
    "text-embedding-3-large",
]
STRATEGIES = [("auto", "Auto — stuff if it fits, else RAG"),
              ("stuff", "Stuff everything"),
              ("rag", "RAG only")]
ARCHIVE_MODES = [("ask", "Ask on close"), ("always", "Always archive"), ("never", "Never archive")]


class SettingsWindow(tk.Toplevel):
    def __init__(self, parent: tk.Misc) -> None:
        super().__init__(parent)
        self.title("Settings — Meeting Workflow")
        self.geometry("700x560")
        self.minsize(600, 460)
        self.transient(parent)

        self._cfg = cfg_mod.get_config()

        # Build variables bound to widgets
        self.v_llm_backend = tk.StringVar(value=self._cfg["llm"]["backend"])
        self.v_claude_model = tk.StringVar(value=self._cfg["llm"]["claude_model"])
        self.v_claude_effort = tk.StringVar(value=self._cfg["llm"]["claude_effort"])
        self.v_claude_effort_summary = tk.StringVar(value=self._cfg["llm"]["claude_effort_summary"])
        self.v_ollama_model = tk.StringVar(value=self._cfg["llm"]["ollama_model"])
        self.v_prefer_small = tk.BooleanVar(value=self._cfg["llm"].get("prefer_small_model", True))

        self.v_embed_backend = tk.StringVar(value=self._cfg["embeddings"]["backend"])
        self.v_openai_model = tk.StringVar(value=self._cfg["embeddings"]["openai_model"])

        self.v_strategy = tk.StringVar(value=self._cfg["context"]["strategy"])
        self.v_budget = tk.IntVar(value=self._cfg["context"]["stuff_budget_tokens"])
        self.v_top_k = tk.IntVar(value=self._cfg["context"]["rag_top_k"])
        self.v_tail = tk.IntVar(value=self._cfg["context"]["rag_query_tail_chars"])
        self.v_include_past = tk.BooleanVar(value=self._cfg["context"].get("include_past_transcripts", False))

        self.v_brief = tk.IntVar(value=self._cfg["cadence"]["brief_interval_sec"])
        self.v_questions = tk.IntVar(value=self._cfg["cadence"]["questions_interval_sec"])
        self.v_points = tk.IntVar(value=self._cfg["cadence"].get("points_interval_sec", 150))
        self.v_auto_idx = tk.IntVar(value=self._cfg["cadence"]["auto_index_interval_sec"])
        self.v_caption_poll = tk.IntVar(value=self._cfg["cadence"]["caption_poll_sec"])

        self.v_anthropic_key = tk.StringVar(value=self._cfg["api_keys"]["anthropic"])
        self.v_openai_key = tk.StringVar(value=self._cfg["api_keys"]["openai"])

        self.v_archive = tk.StringVar(value=self._cfg["archive"]["on_close"])

        self._build_ui()

    def _build_ui(self) -> None:
        nb = ttk.Notebook(self, padding=8)
        nb.pack(fill="both", expand=True)

        nb.add(self._build_llm_tab(nb), text="LLM")
        nb.add(self._build_embeddings_tab(nb), text="Embeddings")
        nb.add(self._build_context_tab(nb), text="Context")
        nb.add(self._build_cadence_tab(nb), text="Cadence")
        nb.add(self._build_keys_tab(nb), text="API keys")
        nb.add(self._build_archive_tab(nb), text="Archive")

        bottom = ttk.Frame(self, padding=(8, 0, 8, 8))
        bottom.pack(fill="x")
        ttk.Button(bottom, text="Cancel", command=self.destroy).pack(side="right")
        ttk.Button(bottom, text="OK", command=self._save_and_close).pack(side="right", padx=(0, 6))
        ttk.Button(bottom, text="Apply", command=self._save).pack(side="right", padx=(0, 6))

    # ---- LLM tab ----
    def _build_llm_tab(self, parent: tk.Misc) -> ttk.Frame:
        f = ttk.Frame(parent, padding=16)
        row = 0
        ttk.Label(f, text="Backend").grid(row=row, column=0, sticky="w", pady=4)
        ttk.Combobox(f, textvariable=self.v_llm_backend, state="readonly",
                     values=["auto", "claude", "ollama"], width=14).grid(row=row, column=1, sticky="w")
        ttk.Label(f, text="Auto = use Claude if API key set, else Ollama",
                  foreground="#666").grid(row=row, column=2, sticky="w", padx=(10, 0))
        row += 1
        ttk.Separator(f, orient="horizontal").grid(row=row, column=0, columnspan=3, sticky="ew", pady=8)
        row += 1
        ttk.Label(f, text="Claude model").grid(row=row, column=0, sticky="w", pady=4)
        ttk.Combobox(f, textvariable=self.v_claude_model, state="readonly",
                     values=CLAUDE_MODELS, width=22).grid(row=row, column=1, sticky="w")
        ttk.Label(f, text="Opus = best, Sonnet = fast+cheap, Haiku = fastest",
                  foreground="#666").grid(row=row, column=2, sticky="w", padx=(10, 0))
        row += 1
        ttk.Label(f, text="Effort (brief / questions)").grid(row=row, column=0, sticky="w", pady=4)
        ttk.Combobox(f, textvariable=self.v_claude_effort, state="readonly",
                     values=EFFORTS, width=10).grid(row=row, column=1, sticky="w")
        ttk.Label(f, text="Background tasks — lower = cheaper, faster",
                  foreground="#666").grid(row=row, column=2, sticky="w", padx=(10, 0))
        row += 1
        ttk.Label(f, text="Effort (Summarize)").grid(row=row, column=0, sticky="w", pady=4)
        ttk.Combobox(f, textvariable=self.v_claude_effort_summary, state="readonly",
                     values=EFFORTS, width=10).grid(row=row, column=1, sticky="w")
        ttk.Label(f, text="One-shot summary — higher = more thorough",
                  foreground="#666").grid(row=row, column=2, sticky="w", padx=(10, 0))
        row += 1
        ttk.Separator(f, orient="horizontal").grid(row=row, column=0, columnspan=3, sticky="ew", pady=8)
        row += 1
        ttk.Label(f, text="Ollama model").grid(row=row, column=0, sticky="w", pady=4)
        ttk.Entry(f, textvariable=self.v_ollama_model, width=24).grid(row=row, column=1, sticky="w")
        ttk.Label(f, text="Leave blank to auto-pick from installed models",
                  foreground="#666").grid(row=row, column=2, sticky="w", padx=(10, 0))
        row += 1
        ttk.Checkbutton(f, text="Prefer a small / lightweight model when auto-picking",
                        variable=self.v_prefer_small).grid(row=row, column=0, columnspan=2, sticky="w", pady=4)
        ttk.Label(f, text="On (default): runs on minimal hardware. Off: prefers the most capable installed model.",
                  foreground="#666").grid(row=row, column=2, sticky="w", padx=(10, 0))
        return f

    # ---- Embeddings tab ----
    def _build_embeddings_tab(self, parent: tk.Misc) -> ttk.Frame:
        f = ttk.Frame(parent, padding=16)
        row = 0
        ttk.Label(f, text="Backend").grid(row=row, column=0, sticky="w", pady=4)
        ttk.Combobox(f, textvariable=self.v_embed_backend, state="readonly",
                     values=["tfidf", "openai"], width=14).grid(row=row, column=1, sticky="w")
        ttk.Label(f, text="tfidf = local keyword. openai = semantic (needs key + costs $)",
                  foreground="#666").grid(row=row, column=2, sticky="w", padx=(10, 0))
        row += 1
        ttk.Label(f, text="OpenAI model").grid(row=row, column=0, sticky="w", pady=4)
        ttk.Combobox(f, textvariable=self.v_openai_model, state="readonly",
                     values=OPENAI_EMBED_MODELS, width=24).grid(row=row, column=1, sticky="w")
        ttk.Label(f, text="3-small = $0.02/1M tok, 3-large = $0.13/1M tok",
                  foreground="#666").grid(row=row, column=2, sticky="w", padx=(10, 0))
        row += 1
        ttk.Separator(f, orient="horizontal").grid(row=row, column=0, columnspan=3, sticky="ew", pady=8)
        row += 1
        ttk.Label(f, text="KB stats", font=("Segoe UI", 9, "bold")).grid(row=row, column=0, sticky="w", pady=(0, 4))
        row += 1
        stats = kb.get_stats()
        ttk.Label(
            f,
            text=(
                f"  chunks: {stats['total_chunks']}\n"
                f"  openai vectors loaded: {stats['openai_vecs_loaded']} "
                f"(dim {stats['openai_vec_dim']}, model {stats['openai_model_loaded'] or 'n/a'})"
            ),
            foreground="#666",
        ).grid(row=row, column=0, columnspan=3, sticky="w")
        return f

    # ---- Context tab ----
    def _build_context_tab(self, parent: tk.Misc) -> ttk.Frame:
        f = ttk.Frame(parent, padding=16)
        row = 0
        ttk.Label(f, text="Strategy").grid(row=row, column=0, sticky="w", pady=4)
        ttk.Combobox(f, textvariable=self.v_strategy, state="readonly",
                     values=[s[0] for s in STRATEGIES], width=10).grid(row=row, column=1, sticky="w")
        legend = " · ".join(f"{k}={v}" for k, v in STRATEGIES)
        ttk.Label(f, text=legend, foreground="#666",
                  wraplength=400).grid(row=row, column=2, sticky="w", padx=(10, 0))
        row += 1
        ttk.Label(f, text="Stuff budget (tokens)").grid(row=row, column=0, sticky="w", pady=4)
        ttk.Spinbox(f, from_=10000, to=900000, increment=10000, width=10,
                    textvariable=self.v_budget).grid(row=row, column=1, sticky="w")
        ttk.Label(f, text="If total context ≤ this, stuff everything (auto mode)",
                  foreground="#666").grid(row=row, column=2, sticky="w", padx=(10, 0))
        row += 1
        ttk.Label(f, text="RAG top-K").grid(row=row, column=0, sticky="w", pady=4)
        ttk.Spinbox(f, from_=1, to=30, width=6,
                    textvariable=self.v_top_k).grid(row=row, column=1, sticky="w")
        ttk.Label(f, text="Chunks retrieved per query in RAG mode",
                  foreground="#666").grid(row=row, column=2, sticky="w", padx=(10, 0))
        row += 1
        ttk.Label(f, text="Query tail (chars)").grid(row=row, column=0, sticky="w", pady=4)
        ttk.Spinbox(f, from_=500, to=20000, increment=500, width=8,
                    textvariable=self.v_tail).grid(row=row, column=1, sticky="w")
        ttk.Label(f, text="Last N chars of transcript used as the retrieval query",
                  foreground="#666").grid(row=row, column=2, sticky="w", padx=(10, 0))
        row += 1
        ttk.Separator(f, orient="horizontal").grid(row=row, column=0, columnspan=3, sticky="ew", pady=10)
        row += 1
        ttk.Checkbutton(f, text="Include past meeting transcripts in retrieval",
                        variable=self.v_include_past).grid(row=row, column=0, columnspan=2, sticky="w", pady=4)
        row += 1
        ttk.Label(
            f,
            text=("OFF (default): each meeting is isolated — retrieval pulls only your uploaded\n"
                  "documents, and the current meeting's transcript is always passed in full.\n"
                  "ON: past meetings stored in the KB can also surface in briefs/questions\n"
                  "(cross-meeting memory)."),
            foreground="#666",
        ).grid(row=row, column=0, columnspan=3, sticky="w")
        return f

    # ---- Cadence tab ----
    def _build_cadence_tab(self, parent: tk.Misc) -> ttk.Frame:
        f = ttk.Frame(parent, padding=16)
        row = 0
        for label, var, helper in [
            ("Brief interval (sec)", self.v_brief, "How often the running brief auto-updates"),
            ("Questions interval (sec)", self.v_questions, "How often suggested questions refresh"),
            ("Chip-in interval (sec)", self.v_points, "How often 'what you could say' suggestions refresh"),
            ("Auto-index interval (sec)", self.v_auto_idx, "How often the in-progress transcript is saved to KB"),
            ("Caption poll (sec)", self.v_caption_poll, "How often the Teams Captions panel is scraped"),
        ]:
            ttk.Label(f, text=label).grid(row=row, column=0, sticky="w", pady=4)
            ttk.Spinbox(f, from_=2, to=3600, increment=2, width=8,
                        textvariable=var).grid(row=row, column=1, sticky="w")
            ttk.Label(f, text=helper, foreground="#666").grid(row=row, column=2, sticky="w", padx=(10, 0))
            row += 1
        return f

    # ---- API keys tab ----
    def _build_keys_tab(self, parent: tk.Misc) -> ttk.Frame:
        f = ttk.Frame(parent, padding=16)
        row = 0
        ttk.Label(f, text="Anthropic key").grid(row=row, column=0, sticky="w", pady=4)
        ttk.Entry(f, textvariable=self.v_anthropic_key, show="*", width=44).grid(row=row, column=1, sticky="w")
        ttk.Label(f, text="sk-ant-…", foreground="#666").grid(row=row, column=2, sticky="w", padx=(10, 0))
        row += 1
        ttk.Label(f, text="OpenAI key").grid(row=row, column=0, sticky="w", pady=4)
        ttk.Entry(f, textvariable=self.v_openai_key, show="*", width=44).grid(row=row, column=1, sticky="w")
        ttk.Label(f, text="sk-… (only needed if embeddings backend = openai)",
                  foreground="#666").grid(row=row, column=2, sticky="w", padx=(10, 0))
        row += 1
        ttk.Separator(f, orient="horizontal").grid(row=row, column=0, columnspan=3, sticky="ew", pady=12)
        row += 1
        ttk.Label(
            f,
            text=(
                "Keys are stored at ~/.meeting_workflow/config.json (chmod 600).\n"
                "Environment variables ANTHROPIC_API_KEY / OPENAI_API_KEY also work."
            ),
            foreground="#666",
        ).grid(row=row, column=0, columnspan=3, sticky="w")
        return f

    # ---- Archive tab ----
    def _build_archive_tab(self, parent: tk.Misc) -> ttk.Frame:
        f = ttk.Frame(parent, padding=16)
        ttk.Label(f, text="When the app closes with un-archived transcript:").pack(anchor="w", pady=(0, 8))
        for value, label in ARCHIVE_MODES:
            ttk.Radiobutton(f, text=label, value=value, variable=self.v_archive).pack(anchor="w")
        ttk.Label(
            f,
            text=(
                "\nArchive = index the current transcript into the KB so future meetings\n"
                "can retrieve from it (transcripts are also auto-indexed every 10 min "
                "while the app is open)."
            ),
            foreground="#666",
        ).pack(anchor="w", pady=(12, 0))
        return f

    # ---- Persistence ----
    def _save(self) -> None:
        new_cfg = copy.deepcopy(self._cfg)
        new_cfg["llm"]["backend"] = self.v_llm_backend.get()
        new_cfg["llm"]["claude_model"] = self.v_claude_model.get()
        new_cfg["llm"]["claude_effort"] = self.v_claude_effort.get()
        new_cfg["llm"]["claude_effort_summary"] = self.v_claude_effort_summary.get()
        new_cfg["llm"]["ollama_model"] = self.v_ollama_model.get().strip()
        new_cfg["llm"]["prefer_small_model"] = bool(self.v_prefer_small.get())
        new_cfg["embeddings"]["backend"] = self.v_embed_backend.get()
        new_cfg["embeddings"]["openai_model"] = self.v_openai_model.get()
        new_cfg["context"]["strategy"] = self.v_strategy.get()
        new_cfg["context"]["stuff_budget_tokens"] = int(self.v_budget.get())
        new_cfg["context"]["rag_top_k"] = int(self.v_top_k.get())
        new_cfg["context"]["rag_query_tail_chars"] = int(self.v_tail.get())
        new_cfg["context"]["include_past_transcripts"] = bool(self.v_include_past.get())
        new_cfg["cadence"]["brief_interval_sec"] = int(self.v_brief.get())
        new_cfg["cadence"]["questions_interval_sec"] = int(self.v_questions.get())
        new_cfg["cadence"]["points_interval_sec"] = int(self.v_points.get())
        new_cfg["cadence"]["auto_index_interval_sec"] = int(self.v_auto_idx.get())
        new_cfg["cadence"]["caption_poll_sec"] = int(self.v_caption_poll.get())
        new_cfg["api_keys"]["anthropic"] = self.v_anthropic_key.get().strip()
        new_cfg["api_keys"]["openai"] = self.v_openai_key.get().strip()
        new_cfg["archive"]["on_close"] = self.v_archive.get()

        # Validation
        if new_cfg["embeddings"]["backend"] == "openai" and not new_cfg["api_keys"]["openai"]:
            messagebox.showwarning(
                "Missing OpenAI key",
                "Embeddings backend is set to OpenAI but no OpenAI API key is configured.\n"
                "Saved anyway, but retrieval will fall back to TF-IDF until you add a key."
            )

        cfg_mod.save_config(new_cfg)
        self._cfg = new_cfg

    def _save_and_close(self) -> None:
        self._save()
        self.destroy()
