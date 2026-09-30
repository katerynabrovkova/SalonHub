// @vitest-environment jsdom
import { render } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { EditProfileFieldProps } from "../EditProfileField";

// The page only passes settings (docs/DECISIONS.md § "Item 10 design details
// (edit name and phone)", point 6); the component's behaviour is covered by
// EditProfileField.test.tsx, so it is replaced here by a spy.
const renderedProps: EditProfileFieldProps[] = [];
vi.mock("../EditProfileField", () => ({
  default: (props: EditProfileFieldProps) => {
    renderedProps.push(props);
    return null;
  },
}));

import EditPhonePage from "./page";

describe("EditPhonePage", () => {
  it("test_renders_edit_profile_field_with_phone_settings", () => {
    render(<EditPhonePage />);

    expect(renderedProps.at(-1)).toEqual({
      field: "phone",
      title: "Змінити телефон",
      label: "Телефон",
      inputType: "tel",
      maxLength: 32,
    });
  });
});
