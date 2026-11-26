// In-page dialogs that replace the browser's confirm()/prompt()/alert(). <DialogHost /> (mounted once in the layout)
// renders them; these functions return promises so call sites read like the native ones:
//   if (!(await confirmBox({ message: "Delete it?", danger: true }))) return;
//   const v = await formBox({ title: "Edit", fields: [{ name: "name", label: "Name", value: "Ann" }] });  // null = cancelled

export type DialogField = {
  name: string;
  label: string;
  value?: string;
  placeholder?: string;
  required?: boolean;
  /** The confirm button stays disabled until the field equals this exactly (type-the-name-to-confirm). */
  mustEqual?: string;
};

export type DialogRequest = {
  title?: string;
  message?: string;
  confirmLabel?: string;
  cancelLabel?: string | null; // null: no cancel button (an info box)
  danger?: boolean;
  fields?: DialogField[];
  resolve: (result: Record<string, string> | boolean | null) => void;
};

let host: ((r: DialogRequest) => void) | null = null;

/** Called by <DialogHost />. Returns the cleanup function. */
export function registerDialogHost(fn: (r: DialogRequest) => void) {
  host = fn;
  return () => { if (host === fn) host = null; };
}

type Opts = Omit<DialogRequest, "resolve" | "fields">;

export function confirmBox(opts: Opts): Promise<boolean> {
  if (!host) return Promise.resolve(typeof window !== "undefined" ? window.confirm(opts.message ?? opts.title ?? "") : false);
  return new Promise((resolve) => host!({ ...opts, resolve: (r) => resolve(r === true) }));
}

export function infoBox(opts: Omit<Opts, "cancelLabel">): Promise<void> {
  return confirmBox({ confirmLabel: "OK", ...opts, cancelLabel: null }).then(() => undefined);
}

export function formBox(opts: Opts & { fields: DialogField[] }): Promise<Record<string, string> | null> {
  if (!host) return Promise.resolve(null);
  return new Promise((resolve) =>
    host!({ ...opts, resolve: (r) => resolve(r && typeof r === "object" ? r : null) }),
  );
}
