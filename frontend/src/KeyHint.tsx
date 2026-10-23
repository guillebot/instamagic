type KeyHintProps = {
  children: string;
};

/** Visible keyboard shortcut badge; decorative (shortcut also works globally). */
export function KeyHint({ children }: KeyHintProps) {
  return (
    <kbd className="key-hint" aria-hidden="true">
      {children}
    </kbd>
  );
}

type ActionLabelProps = {
  label: string;
  shortcut: string;
};

export function ActionLabel({ label, shortcut }: ActionLabelProps) {
  return (
    <>
      {label} <KeyHint>{shortcut}</KeyHint>
    </>
  );
}
