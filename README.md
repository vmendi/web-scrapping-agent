# Course Agent

Course Agent is a collection of browser automation agents that orchestrate OpenAI’s computer-use models (via the [`browser-use`](https://github.com/browser-use/browser-use) SDK) to explore university course catalogs and extract structured data. The repository contains the “brain” agent that plans the work, domain-specific navigator/crawler/extractor agents, and the tooling layer used to drive a Chrome session through the CDP (Chrome DevTools Protocol).

## Highlights
- **Brain-driven workflow:** `my_brain_agent.py` coordinates long-running jobs using system/user prompts in `my_brain_system_02.md` and `my_brain_user_01.md`.
- **Specialised agents:** Navigator, crawler, and extractor agents (`my_navigator_agent.py`, `my_crawler_agent.py`, `my_extractor_agent.py`) encapsulate goal-specific logic while sharing a common tool interface.
- **Browser-use tooling:** `my_agent_tools.py` wraps CDP actions (navigation, clicks, scrolling, tab control, file downloads, etc.) and exposes them to the LLM.
- **Run state capture:** Each step persists prompts, tool calls, and screenshots under `output/<run_id>/`, making it easy to audit or debug a run.
- **History utilities:** `history_logger.py` can strip screenshots and dump compact JSON summaries for completed sessions.

## Development Notes

- The extractor agent currently short-circuits with a dummy `ActionResult` (see `my_extractor_agent.py`). Remove that early `return` to exercise the step loop.
- Review `todo.md` for outstanding items such as improving navigator error messaging and experimenting with alternative Markdown converters.
- Keep prompts in sync with agent capabilities; editing the `my_*_system_*.md` / `my_*_user_*.md` files is the quickest way to tweak behaviour.
