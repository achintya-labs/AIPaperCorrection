import { api, ApiError } from "./api";
import { confirmBox, formBox } from "./dialog";

const needsForce = (e: unknown): e is ApiError => e instanceof ApiError && e.status === 409 && /force/.test(e.message);

/**
 * Delete a paper after confirming. If the server says it holds TA-reviewed work (409 mentioning force),
 * ask again with the server's own wording before forcing. Returns whether anything was deleted.
 * Other errors (e.g. "being graded right now") are thrown for the caller to show.
 */
export async function deletePaperFlow(paper: { id: number; label: string }): Promise<boolean> {
  const ok = await confirmBox({
    title: `Delete the paper for ${paper.label}?`,
    message: "Its page images, Claude's grades and any TA review work are permanently deleted. The student record is kept. This can't be undone.",
    confirmLabel: "Delete", danger: true,
  });
  if (!ok) return false;
  try {
    await api(`/submissions/${paper.id}`, { method: "DELETE" });
    return true;
  } catch (e) {
    if (!needsForce(e)) throw e;
    if (!(await confirmBox({ title: "Delete anyway?", message: e.message, confirmLabel: "Delete anyway", danger: true }))) return false;
    await api(`/submissions/${paper.id}?force=true`, { method: "DELETE" });
    return true;
  }
}

/**
 * Delete an exam. An exam that still has papers takes a second step: the server's counts are shown and
 * the admin must type the exam's name, because this removes every paper and grade in it.
 */
export async function deleteExamFlow(exam: { id: number; name: string }): Promise<boolean> {
  const ok = await confirmBox({
    title: `Delete the exam “${exam.name}”?`,
    message: "All its rubric versions are permanently deleted. If it has papers you will be shown how many before anything is removed.",
    confirmLabel: "Delete", danger: true,
  });
  if (!ok) return false;
  try {
    await api(`/exams/${exam.id}`, { method: "DELETE" });
    return true;
  } catch (e) {
    if (!needsForce(e)) throw e;
    const typed = await formBox({
      title: "Delete the exam and all its papers?",
      message: `${e.message}\n\nThis permanently deletes the exam, all of its papers, their page images and every grade.`,
      fields: [{ name: "name", label: `To confirm, type the exam name: ${exam.name}`, mustEqual: exam.name }],
      confirmLabel: "Delete exam and papers",
      danger: true,
    });
    if (!typed) return false;
    await api(`/exams/${exam.id}?force=true`, { method: "DELETE" });
    return true;
  }
}
