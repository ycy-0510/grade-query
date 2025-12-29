## 2024-05-23 - N+1 Query Optimization in Excel Generation
**Learning:** The `generate_grades_excel` function was performing N+1 queries by fetching student grades individually inside a loop. This is a common pattern when reusing logic designed for a single-user view (`calculate_student_grades`) in a bulk export function.
**Action:** Refactor the calculation function to accept pre-fetched data (dependency injection style) so that bulk operations can fetch all necessary data in a few constant queries (O(1)) instead of O(N) queries.
