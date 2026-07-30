import type { TraceDigest } from "../../types";
import styles from "./DigestPanel.module.css";

interface Props {
  digest: TraceDigest;
}

const GROUPS: Array<{
  key: "decisions" | "dead_ends" | "learnings";
  label: string;
}> = [
  { key: "decisions", label: "Key decisions" },
  { key: "dead_ends", label: "Dead ends" },
  { key: "learnings", label: "Learnings" },
];

export function DigestPanel({ digest }: Props) {
  const ask = (digest.ask ?? "").trim();
  const groups = GROUPS.map(({ key, label }) => ({
    key,
    label,
    items: (digest[key] ?? [])
      .filter((s) => s.trim() !== "")
      .map((s) => s.trim()),
  })).filter((g) => g.items.length > 0);
  if (!ask && groups.length === 0) return null;
  return (
    <div className={styles.wrap}>
      <section className={styles.panel}>
        <div className={styles.eyebrow}>
          <span className={styles.badge}>ai digest</span>
          <span className={styles.note}>generated on upload</span>
        </div>
        <div className={styles.bullets}>
          {ask && (
            <div className={styles.row}>
              <div className={styles.label}>Ask</div>
              <div className={styles.value}>{ask}</div>
            </div>
          )}
          {groups.map(({ key, label, items }) => (
            <div className={styles.row} key={key}>
              <div className={styles.label}>{label}</div>
              {items.length === 1 ? (
                <div className={styles.value}>{items[0]}</div>
              ) : (
                <ul className={styles.itemList}>
                  {items.map((item, i) => (
                    <li key={i}>{item}</li>
                  ))}
                </ul>
              )}
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}
