# Assignment 03 Part E query coverage

- Generated: 2026-11-27T13:39:22.925083+00:00
- Embedding model: `nomic-embed-text:latest`
- Similarity threshold: 0.80
- Minimum coverage: 85.00%
- Coverage: **47.50%** (19/40)

## Threshold justification

The 0.80 cosine floor is rounded down from the measured 0.8311 mean similarity for 15 production-provider paraphrase pairs in Part B, while remaining well above the measured 0.3619 unrelated-pair mean. The 85% minimum uses the paper's example that coverage below 85% flags a gap.

## All live queries

| Query | Topic | Covered | Nearest golden | Similarity |
| --- | --- | --- | --- | ---: |
| lq01 | attendance | no | q03: What is the consequence of failing to meet a course's attendance requirements? | 0.703486 |
| lq02 | attendance | no | q02: How much absence may the HoD condone for genuine reasons under clause 1.21? | 0.688138 |
| lq03 | attendance | no | q18: According to Batch B, what attendance threshold is required to sit for the final examination? | 0.796122 |
| lq04 | academic_warning | yes | q04: What minimum CGPA is required to avoid academic warning in the June 2026 handbook? | 0.821131 |
| lq05 | academic_warning | no | q05: When is the academic-warning count reset after a warning? | 0.771139 |
| lq06 | academic_warning | yes | q22: What restriction applies to students on a second academic warning? | 0.824338 |
| lq07 | course_registration | no | q06: What must a student do with a failed or missed course from a previous semester? | 0.799342 |
| lq08 | course_repeat | no | q07: May a student repeat a course to improve CGPA under the June 2026 rules? | 0.781468 |
| lq09 | grading | yes | q21: What are the grade points for A+ and F in the June 2026 handbook? | 0.806915 |
| lq10 | fyp | yes | q23: In which semester must a student normally register for FYP-I? | 0.884330 |
| lq11 | theory_lab | yes | q24: If a student passes Theory but fails Lab, which component must be repeated? | 0.879466 |
| lq12 | theory_lab | yes | q24: If a student passes Theory but fails Lab, which component must be repeated? | 0.841853 |
| lq13 | generative_ai | no | q08: What does the Student Handbook require when students submit work containing Generative AI output? | 0.706292 |
| lq14 | generative_ai | no | q30: Are students allowed to use Generative AI for assessments under the 2026 Student Handbook? | 0.701783 |
| lq15 | generative_ai | no | q08: What does the Student Handbook require when students submit work containing Generative AI output? | 0.769985 |
| lq16 | annual_report_faculty | yes | q25: How many faculty members are listed in the 2023-24 annual report faculty summary? | 0.918301 |
| lq17 | annual_report_faculty | yes | q09: In the Annual Report 2019-20, how many faculty members are listed in the university-wide summary? | 0.862542 |
| lq18 | annual_report_faculty | yes | q10: In the Annual Report 2023-24, what is the university-wide faculty total? | 0.903322 |
| lq19 | attendance_clause | yes | q11: Which clause number in the June 2026 Academic Rules Handbook contains the attendance requirement? | 0.885958 |
| lq20 | scholarship | yes | q19: What CGPA and credit-hours requirement appears in the Batch B scholarship eligibility record? | 0.833361 |
| lq21 | scholarship | no | q19: What CGPA and credit-hours requirement appears in the Batch B scholarship eligibility record? | 0.745269 |
| lq22 | batch_b_attendance | no | q18: According to Batch B, what attendance threshold is required to sit for the final examination? | 0.755116 |
| lq23 | policy_drift | no | q01: What attendance level does the June 2026 Academic Rules Handbook require? | 0.741112 |
| lq24 | batch_b_policy | yes | q20: What CGPA continuation requirement is stated in the Batch B 2026 academic policy? | 0.948001 |
| lq25 | document_versioning | no | q20: What CGPA continuation requirement is stated in the Batch B 2026 academic policy? | 0.729825 |
| lq26 | course_repeat | no | q06: What must a student do with a failed or missed course from a previous semester? | 0.757643 |
| lq27 | academic_warning | yes | q04: What minimum CGPA is required to avoid academic warning in the June 2026 handbook? | 0.811859 |
| lq28 | generative_ai | no | q29: Under the Student Handbook, how should Generative AI use be acknowledged in submitted work? | 0.707644 |
| lq29 | campus_transport | yes | q31: What route schedule does the Islamabad campus shuttle follow in the evening? | 0.854098 |
| lq30 | admissions_deadline | yes | q32: When is the application deadline for the next undergraduate admissions intake? | 0.992606 |
| lq31 | tuition_fees | yes | q33: What semester tuition fee applies to a first-year BS computer science student? | 0.966907 |
| lq32 | placement_services | yes | q34: Which office handles software engineering internship placement and registration? | 0.867584 |
| lq33 | sports_trials | no | q32: When is the application deadline for the next undergraduate admissions intake? | 0.590202 |
| lq34 | library_hours | yes | q35: Until what time is the Islamabad campus library open on Fridays? | 1.000000 |
| lq35 | campus_wifi | no | q06: What must a student do with a failed or missed course from a previous semester? | 0.570162 |
| lq36 | student_counseling | no | q23: In which semester must a student normally register for FYP-I? | 0.541347 |
| lq37 | health_services | yes | q36: What are the weekend opening hours for the campus student medical clinic? | 0.888498 |
| lq38 | transcript_services | no | q26: What are the final-examination dates for every program next semester? | 0.590079 |
| lq39 | campus_parking | no | q11: Which clause number in the June 2026 Academic Rules Handbook contains the attendance requirement? | 0.592559 |
| lq40 | alumni_services | no | q06: What must a student do with a failed or missed course from a previous semester? | 0.593325 |

## Uncovered queries by topic

### academic_warning

- `lq05` (0.771139, nearest `q05`): After getting a warning, how do I get the warning count cleared?

### alumni_services

- `lq40` (0.593325, nearest `q06`): How can an alumnus request a replacement degree-verification letter?

### attendance

- `lq01` (0.703486, nearest `q03`): I missed a few classes. What overall attendance percentage am I expected to keep?
- `lq02` (0.688138, nearest `q02`): Can the head of department excuse some absences if I had a genuine reason?
- `lq03` (0.796122, nearest `q18`): What happens to my final exam eligibility when I fall below the attendance rule?

### batch_b_attendance

- `lq22` (0.755116, nearest `q18`): What minimum class attendance is written in the valid attendance record from the second batch?

### campus_parking

- `lq39` (0.592559, nearest `q11`): Do students need a parking sticker, and where is visitor parking?

### campus_wifi

- `lq35` (0.570162, nearest `q06`): How do I reset my campus Wi-Fi password if I forgot it?

### course_registration

- `lq07` (0.799342, nearest `q06`): If I failed a course last semester, can I postpone registering for it?

### course_repeat

- `lq08` (0.781468, nearest `q07`): Can I register again for a passed course because I want a better CGPA?
- `lq26` (0.757643, nearest `q06`): I was absent from a registered course last term. When do I need to take it again?

### document_versioning

- `lq25` (0.729825, nearest `q20`): Why should the 2025 Batch B academic policy be excluded after the 2026 copy arrives?

### generative_ai

- `lq13` (0.706292, nearest `q08`): When I use a GenAI tool in an assignment, what disclosure does the handbook want?
- `lq14` (0.701783, nearest `q30`): Does the student handbook permit ChatGPT-style output in a graded assessment?
- `lq15` (0.769985, nearest `q08`): What is the official name of the student handbook section about AI-based tools?
- `lq28` (0.707644, nearest `q29`): If submitted work includes AI-generated text, should I cite the tool or the original source?

### policy_drift

- `lq23` (0.741112, nearest `q01`): The Batch B attendance note says 75%, while the handbook says something else. Which value is in the latest indexed policy?

### scholarship

- `lq21` (0.745269, nearest `q19`): How many credit hours must a scholarship applicant complete during the year?

### sports_trials

- `lq33` (0.590202, nearest `q32`): When are football team trials held and who is eligible to attend?

### student_counseling

- `lq36` (0.541347, nearest `q23`): Can a student book a confidential counseling session on campus?

### transcript_services

- `lq38` (0.590079, nearest `q26`): Where do graduates submit a request for an official transcript?

