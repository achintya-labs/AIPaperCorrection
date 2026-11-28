export type Role = "admin" | "ta";
export type SubmissionStatus =
  | "uploaded" | "queued" | "grading" | "ready_for_review" | "reviewed" | "failed";
export type GradeStatus = "pending" | "reviewed" | "overridden";

export interface User { id: number; email: string; name: string; role: Role; permissions: string[] }
export interface UserAdmin extends User { active: boolean; has_password: boolean; assigned_papers: number; reviewed_grades: number }
export interface Exam { id: number; name: string; model_override: string | null }
export interface Criterion { description: string; points: number }
export interface Question {
  id?: number;
  number: string;
  text: string;
  max_points: number;
  expected_answer: string;
  criteria: Criterion[];
}
export interface Rubric { id: number; exam_id: number; version: number; questions: Question[] }
export interface OverviewSeries { count: number; mean: number | null; std_dev: number | null }
export interface OverviewBin { start: number; end: number; graded: number; reviewed: number }
export interface ExamOverview {
  exam_id: number;
  exam_name: string;
  total_papers: number;
  max_score: number | null;
  graded: OverviewSeries;
  reviewed: OverviewSeries;
  bin_width: number;
  auto_bin_width: number;
  bins: OverviewBin[];
}
export interface Student { id: number; external_id: string; name: string }

export interface Submission {
  id: number;
  exam_id: number;
  student: Student;
  ta_ids: number[];
  status: SubmissionStatus;
  overall_confidence: number | null;
  grades_total: number;
  grades_reviewed: number;
  min_confidence: number | null;
  score_total: number | null;
  max_score: number | null;
}
export interface Page {
  index: number;
  question_numbers: string[];
  transcription: string;
  confidence: number | null;
}
export interface Grade {
  id: number;
  question_id: number;
  question_number: string;
  question_text: string;
  expected_answer: string;
  criteria: Criterion[];
  max_points: number;
  ai_score: number | null;
  ai_feedback: string;
  ai_note: string;
  ai_confidence: number | null;
  final_score: number | null;
  final_feedback: string | null;
  status: GradeStatus;
  version: number;
  reviewed_by: number | null;
}
export interface SubmissionDetail extends Submission { error: string | null; pages: Page[]; grades: Grade[] }

export interface ImportItem {
  student_external_id: string;
  filename: string;
  pages: number | null;
  status: "ok" | "conflict" | "error";
  detail: string;
  ta_emails: string[];
  submission_id: number | null;
}
export interface ImportResult { dry_run: boolean; created: number; items: ImportItem[] }

export interface ProposedSplit {
  student_external_id: string;
  student_name: string;
  first_page: number;
  last_page: number;
  confidence: number | null;
}
export type SplitJobStatus = "queued" | "processing" | "ready" | "failed";
export interface AutoSplitResult {
  split_token: string;
  filename: string;
  page_count: number;
  status: SplitJobStatus;
  error: string | null;
  allowed_roll_numbers: string[];
  roll_names: Record<string, string>;
  splits: ProposedSplit[];
  existing_student_ids: string[];
}

export interface UsageWindow {
  key: string;
  label: string;
  used_percent: number | null; // 0-100
  resets_at: string | null; // ISO-8601
  status: string | null; // allowed | allowed_warning | rejected
  detail: string | null;
}
export interface Usage {
  source: "subscription" | "api_key";
  model: string;
  checked_at: string;
  windows: UsageWindow[];
}
