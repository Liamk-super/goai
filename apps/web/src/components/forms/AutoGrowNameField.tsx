"use client";

import { useLayoutEffect, useRef, type RefObject } from "react";

export function AutoGrowNameField({
  value,
  onChange,
  placeholder,
  autoFocus = false,
  inputRef,
}: {
  value: string;
  onChange(value: string): void;
  placeholder: string;
  autoFocus?: boolean;
  inputRef?: RefObject<HTMLTextAreaElement | null>;
}) {
  const localRef = useRef<HTMLTextAreaElement>(null);
  const ref = inputRef ?? localRef;

  useLayoutEffect(() => {
    const element = ref.current;
    if (!element) return;
    element.style.height = "auto";
    element.style.height = `${Math.min(element.scrollHeight, 104)}px`;
  }, [ref, value]);

  return (
    <textarea
      ref={ref}
      className="product-name-input"
      rows={1}
      required
      minLength={2}
      maxLength={200}
      autoFocus={autoFocus}
      value={value}
      onChange={event => onChange(event.target.value)}
      placeholder={placeholder}
    />
  );
}
