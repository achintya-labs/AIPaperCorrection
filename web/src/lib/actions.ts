import { api, ApiError } from "./api";

const needsForce = (e: unknown): e is ApiError => e instanceof ApiError && e.status === 409 && /force/.test(e.message);

/**
 * Delete a paper after confirming. If the server says it holds TA-reviewed work (409 mentioning force),
 * ask again with the server's own wording before forcing. Returns whether anything was deleted.
 * Other errors (e.g. "being graded right now") are thrown for the caller to show.
 */
export async function deletePaperFlow(paper: { id: number; label: string }): Promise<boolean> {
  const ok = confirm(
    `Delete the paper for ${paper.label}?\n\nIts page images, Claude's grades and any TA review work are permanently deleted. ` +
    "The student record is kept. This can't be undone.",
  );
  if (!ok) return false;
  try {
    await api(`/submissions/${paper.id}`, { method: "DELETE" });
    return true;
  } catch (e) {
    if (!needsForce(e)) throw e;
    if (!confirm(`${e.message}\n\nDelete it anyway?`)) return false;
    await api(`/submissions/${paper.id}?force=true`, { method: "DELETE" });
    return true;
  }
}

/**
 * Delete an exam. An exam that still has papers takes a second step: the server's counts are shown and
 * the admin must type the exam's name, because this removes every paper and grade in it.
 */
export async function deleteExamFlow(exam: { id: number; name: string }): Promise<boolean> {
  const ok = confirm(
    `Delete the exam “${exam.name}”?\n\nAll its rubric versions are permanently deleted. ` +
    "If it has papers you will be shown how many before anything is removed.",
  );
  if (!ok) return false;
  try {
    await api(`/exams/${exam.id}`, { method: "DELETE" });
    return true;
  } catch (e) {
    if (!needsForce(e)) throw e;
    const typed = prompt(
      `${e.message}\n\nThis permanently deletes the exam, all of its papers, their page images and every grade.\n` +
      `To confirm, type the exam name exactly:\n${exam.name}`,
    );
    if (typed === null) return false;
    if (typed.trim() !== exam.name) {
      alert("That didn't match the exam name, so nothing was deleted.");
      return false;
    }
    await api(`/exams/${exam.id}?force=true`, { method: "DELETE" });
    return true;
  }
}
