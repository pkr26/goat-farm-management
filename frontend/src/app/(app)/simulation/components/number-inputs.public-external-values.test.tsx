import { fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { expect, it } from "vitest";
import { LanguageProvider } from "@/lib/i18n";
import { NumberArrayInput, NumberInput } from "./number-inputs";

it("highlights a restored fractional integer and removes the error after a whole-number correction", () => {
  function Editor() { const [value, setValue] = useState(50.5); return <NumberInput id="heads" aria-label="Heads" value={value} integer onCommit={setValue} />; }
  render(<LanguageProvider><Editor /></LanguageProvider>);
  const field = screen.getByLabelText("Heads");
  expect(field).toHaveAttribute("aria-invalid", "true");
  expect(screen.getByRole("alert")).toHaveTextContent("Enter a whole number.");
  fireEvent.change(field, { target: { value: "50" } }); fireEvent.blur(field);
  expect(field).toHaveValue(50); expect(field).not.toHaveAttribute("aria-invalid"); expect(screen.queryByRole("alert")).toBeNull();
});

it("revalidates external scalar values without replacing an active invalid blank draft", () => {
  function Editor() { const [value, setValue] = useState(50); return <><NumberInput id="heads" aria-label="Heads" value={value} integer onCommit={setValue} /><button onClick={() => setValue(Infinity)}>Restore unusable count</button><button onClick={() => setValue(60)}>Restore valid count</button></>; }
  render(<LanguageProvider><Editor /></LanguageProvider>); const field = screen.getByLabelText("Heads");
  fireEvent.click(screen.getByText("Restore unusable count")); expect(field).toHaveAttribute("aria-invalid", "true"); expect(screen.getByRole("alert")).toHaveTextContent("Enter a valid number.");
  fireEvent.click(screen.getByText("Restore valid count")); expect(field).toHaveValue(60); expect(screen.queryByRole("alert")).toBeNull();
  fireEvent.change(field, { target: { value: "" } }); fireEvent.click(screen.getByText("Restore unusable count"));
  expect(field).toHaveValue(null); expect(screen.getByRole("alert")).toHaveTextContent("A value is required.");
  fireEvent.click(screen.getByText("Restore valid count")); expect(field).toHaveValue(null); expect(field).toHaveAttribute("aria-invalid", "true");
});

it("highlights an initially incomplete numeric array and accepts a complete correction", () => {
  function Editor() { const [value, setValue] = useState([2.5, 6]); return <NumberArrayInput id="curve" value={value} rule={{ exactLength: 3, nondecreasing: true, itemLabel: "weights" }} onCommit={setValue} onValidityChange={() => {}} />; }
  render(<LanguageProvider><label htmlFor="curve">Curve</label><Editor /></LanguageProvider>); const field = screen.getByLabelText("Curve");
  expect(field).toHaveAttribute("aria-invalid", "true"); expect(screen.getByRole("alert")).toHaveTextContent("Enter exactly 3 weights.");
  fireEvent.change(field, { target: { value: "2.5, 6, 9.5" } }); fireEvent.blur(field); expect(field).toHaveValue("2.5, 6, 9.5"); expect(screen.queryByRole("alert")).toBeNull();
});

it("revalidates externally changed array contents while preserving an invalid local draft", () => {
  function Editor() { const [value, setValue] = useState([2.5, 6, 9.5]); return <><NumberArrayInput id="curve" value={value} rule={{ exactLength: 3, nondecreasing: true, itemLabel: "weights" }} onCommit={setValue} onValidityChange={() => {}} /><button onClick={() => setValue([7, 6, 9.5])}>Update birth point</button><button onClick={() => setValue([3, 6, 9.5])}>Restore valid curve</button></>; }
  render(<LanguageProvider><label htmlFor="curve">Curve</label><Editor /></LanguageProvider>); const field = screen.getByLabelText("Curve");
  fireEvent.click(screen.getByText("Update birth point")); expect(field).toHaveValue("7, 6, 9.5"); expect(field).toHaveAttribute("aria-invalid", "true"); expect(screen.getByRole("alert")).toHaveTextContent("Weights must not decrease.");
  fireEvent.click(screen.getByText("Restore valid curve")); expect(screen.queryByRole("alert")).toBeNull();
  fireEvent.change(field, { target: { value: "3,,6" } }); fireEvent.click(screen.getByText("Update birth point")); fireEvent.click(screen.getByText("Restore valid curve"));
  expect(field).toHaveValue("3,,6"); expect(field).toHaveAttribute("aria-invalid", "true"); expect(screen.getByRole("alert")).toHaveTextContent("Enter only comma-separated numbers.");
});
