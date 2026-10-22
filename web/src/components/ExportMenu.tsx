import { useEffect, useId, useRef, useState } from 'react';
import type { KeyboardEvent } from 'react';
import type { ExportFilters, ExportArea, ExportFormat, ExportMode } from '../api/types';
import { useExports } from '../state/ExportsContext';
import styles from './ExportMenu.module.css';

interface Option {
  format: ExportFormat;
  mode: ExportMode;
  label: string;
  hint: string;
}

const OPTIONS: Option[] = [
  { format: 'csv', mode: 'summary', label: 'CSV – Summary', hint: 'One row per event' },
  { format: 'csv', mode: 'detailed', label: 'CSV – Detailed', hint: 'Includes field changes' },
  { format: 'xlsx', mode: 'summary', label: 'Excel (XLSX) – Summary', hint: 'One row per event' },
  {
    format: 'xlsx',
    mode: 'detailed',
    label: 'Excel (XLSX) – Detailed',
    hint: 'Includes field changes',
  },
];

interface Props {
  area: ExportArea;
  filters: ExportFilters;
  disabled?: boolean;
}

/** WAI-ARIA menu button: Enter/Space/ArrowDown opens, arrows move, Escape closes. */
export function ExportMenu({ area, filters, disabled = false }: Props) {
  const { queueExport } = useExports();
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const itemRefs = useRef<(HTMLButtonElement | null)[]>([]);
  const rootRef = useRef<HTMLDivElement>(null);
  const menuId = useId();
  const focusIndex = useRef(0);

  useEffect(() => {
    if (!open) return;
    itemRefs.current[focusIndex.current]?.focus();
    const onPointerDown = (e: PointerEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('pointerdown', onPointerDown);
    return () => document.removeEventListener('pointerdown', onPointerDown);
  }, [open]);

  const openAt = (index: number) => {
    focusIndex.current = index;
    setOpen(true);
  };

  const close = (restoreFocus = true) => {
    setOpen(false);
    if (restoreFocus) buttonRef.current?.focus();
  };

  const onButtonKeyDown = (e: KeyboardEvent<HTMLButtonElement>) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      openAt(0);
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      openAt(OPTIONS.length - 1);
    }
  };

  const onMenuKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    const current = itemRefs.current.findIndex((el) => el === document.activeElement);
    const move = (index: number) =>
      itemRefs.current[(index + OPTIONS.length) % OPTIONS.length]?.focus();
    switch (e.key) {
      case 'ArrowDown':
        e.preventDefault();
        move(current + 1);
        break;
      case 'ArrowUp':
        e.preventDefault();
        move(current - 1);
        break;
      case 'Home':
        e.preventDefault();
        move(0);
        break;
      case 'End':
        e.preventDefault();
        move(OPTIONS.length - 1);
        break;
      case 'Escape':
        e.preventDefault();
        close();
        break;
      case 'Tab':
        setOpen(false);
        break;
    }
  };

  const choose = async (option: Option) => {
    close();
    setBusy(true);
    try {
      await queueExport({ area, mode: option.mode, format: option.format, filters });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className={styles.root} ref={rootRef}>
      <button
        ref={buttonRef}
        type="button"
        className="btn btn-primary"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? menuId : undefined}
        disabled={disabled || busy}
        onClick={() => (open ? close() : openAt(0))}
        onKeyDown={onButtonKeyDown}
      >
        {busy ? 'Queuing…' : 'Export'}
        <span aria-hidden="true" className={styles.caret}>
          ▾
        </span>
      </button>
      {open && (
        <div
          id={menuId}
          role="menu"
          aria-label="Export format"
          className={styles.menu}
          onKeyDown={onMenuKeyDown}
        >
          {OPTIONS.map((option, i) => (
            <button
              key={`${option.format}-${option.mode}`}
              ref={(el) => {
                itemRefs.current[i] = el;
              }}
              type="button"
              role="menuitem"
              tabIndex={-1}
              className={styles.item}
              onClick={() => void choose(option)}
            >
              <span>{option.label}</span>
              <span className={styles.hint} aria-hidden="true">
                {option.hint}
              </span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
