# Test scope

Run the complete suite from the skill directory:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -p 'test_*.py' -v
```

Keep one owning suite per contract. Retain representative success, failure and
recovery paths, plus integration cases for real incidents. Use table-driven cases
for equivalent inputs, not another module per incident or layers of test runners.
Do not test the presence of particular English sentences as proof of behavior.
`test_skill_structure.py` checks entrypoint structure; policy meaning belongs to
review and the existing behavioral evaluations, whose scenarios remain intact.

Do not freeze recipe wording, YAML layout, dependency lists, optimizer settings,
or arbitrary document/test counts. Exercise the resolved configuration or actual
output instead. Fixture self-checks and canned dispatcher echoes do not add
behavioral coverage. Publication permissions are tested at authorization/write
boundaries, not through Docker runtime installation order.

- `test_background_policy.py`: guidance routing, independent prompts, blank
  judgments and stops; no AST extraction or assertions about exact prompt wording.
- `test_preparation_feedback.py`: complete redacted diagnostics, adjacent blank
  admission contract, domain hints and bounded/read-only archive pages.
- `test_preparation_feedback_integration.py`: complete update/addition preparation
  and receipt → archive page → exact-member extraction/revalidation.
- `test_source_material*.py`: archive safety and immutable source lineage. Do not
  duplicate their traversal/symlink/entry-limit matrices in pagination tests.
- `test_review_projection.py`, `test_review_materials.py`: exact publication files,
  scoped public prose and immutable evidence. Error wording is not the contract;
  assert structured fields/codes where provided, otherwise rejection/no mutation.
- `test_render_smoke.py`: exact source/XML bindings and rejection of blank or
  tampered frames. CI Python versions are configuration, not a test contract.
- `test_svg_optimization.py`: real helper/CLI failure and integrity handling;
  installed-SVGO cases check retained SVG content rather than plugin-list snapshots.
- `test_research_env.py`: per-bank environment limits, budget enforcement and
  frozen configuration across task start, CLI calls and doctor checks.

Merge/approval/deadline, publication permissions, exact-byte validation, state
conflicts and lost-write recovery remain covered by their existing executable
suites. Never remove a failing safety case merely to make CI green. Fixture images
and declared judgments do not establish real renderer or model performance.
