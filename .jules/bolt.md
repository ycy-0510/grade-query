# Bolt's Journal

## 2024-05-22 - [Project Initialization]
**Learning:** Initialized Bolt's journal to track critical performance learnings.
**Action:** Always check this journal before starting new performance tasks.

## 2024-05-22 - [Bulk Data Fetching in Reports]
**Learning:** N+1 queries in report generation (e.g., `generate_grades_excel`) can be severe. Simply pre-fetching related data (scores, exams) and grouping in Python reduced queries from ~2*N to ~3 total.
**Action:** For any export/report function, avoid calling complex calculation functions that fetch their own data inside a loop. Instead, bulk fetch and inject data.
