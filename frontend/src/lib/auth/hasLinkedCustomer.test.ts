import { describe, expect, it } from "vitest";

import { hasLinkedCustomer } from "./hasLinkedCustomer";
import type { Me } from "./Me";

const BASE_ME: Me = {
  email: "alice@example.com",
  role: "client",
  name: null,
  phone: null,
  email_verified: true,
};

describe("hasLinkedCustomer", () => {
  it("test_non_empty_name_means_linked", () => {
    expect(hasLinkedCustomer({ ...BASE_ME, name: "Alice" })).toBe(true);
  });

  it("test_null_name_means_not_linked", () => {
    expect(hasLinkedCustomer({ ...BASE_ME, name: null })).toBe(false);
  });

  it("test_empty_string_name_still_means_linked_not_truthiness", () => {
    expect(hasLinkedCustomer({ ...BASE_ME, name: "" })).toBe(true);
  });
});
