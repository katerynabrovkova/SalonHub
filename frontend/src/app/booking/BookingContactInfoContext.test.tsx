// @vitest-environment jsdom
import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { BookingContactInfoProvider, useBookingContactInfo } from "./BookingContactInfoContext";

function ContactInfoReadout() {
  const { customerName, customerEmail, customerPhone, setCustomerName } = useBookingContactInfo();
  return (
    <div>
      <span data-testid="name">{customerName}</span>
      <span data-testid="email">{customerEmail}</span>
      <span data-testid="phone">{customerPhone}</span>
      <button onClick={() => setCustomerName("Alice")}>set name</button>
    </div>
  );
}

describe("BookingContactInfoContext", () => {
  it("test_initial_state_is_empty_strings_for_all_three_fields", () => {
    render(
      <BookingContactInfoProvider>
        <ContactInfoReadout />
      </BookingContactInfoProvider>,
    );

    expect(screen.getByTestId("name")).toHaveTextContent("");
    expect(screen.getByTestId("email")).toHaveTextContent("");
    expect(screen.getByTestId("phone")).toHaveTextContent("");
  });

  it("test_setters_update_each_field_independently", async () => {
    const user = userEvent.setup();

    function AllFieldsReadout() {
      const {
        customerName,
        customerEmail,
        customerPhone,
        setCustomerName,
        setCustomerEmail,
        setCustomerPhone,
      } = useBookingContactInfo();
      return (
        <div>
          <span data-testid="name">{customerName}</span>
          <span data-testid="email">{customerEmail}</span>
          <span data-testid="phone">{customerPhone}</span>
          <button onClick={() => setCustomerName("Alice")}>set name</button>
          <button onClick={() => setCustomerEmail("alice@example.com")}>set email</button>
          <button onClick={() => setCustomerPhone("+10000000000")}>set phone</button>
        </div>
      );
    }

    render(
      <BookingContactInfoProvider>
        <AllFieldsReadout />
      </BookingContactInfoProvider>,
    );

    await user.click(screen.getByRole("button", { name: "set email" }));

    expect(screen.getByTestId("email")).toHaveTextContent("alice@example.com");
    expect(screen.getByTestId("name")).toHaveTextContent("");
    expect(screen.getByTestId("phone")).toHaveTextContent("");
  });

  it(
    "test_value_persists_across_a_simulated_rerender_of_children_with_the_same_provider_instance",
    async () => {
      const user = userEvent.setup();

      function OtherChild() {
        return <p>a different child than before</p>;
      }

      const { rerender } = render(
        <BookingContactInfoProvider>
          <ContactInfoReadout />
        </BookingContactInfoProvider>,
      );

      await user.click(screen.getByRole("button", { name: "set name" }));
      expect(screen.getByTestId("name")).toHaveTextContent("Alice");

      // Re-renders the SAME Provider element (not a fresh
      // <BookingContactInfoProvider> instance) with different children in
      // its subtree — the shape of a step-to-step navigation where
      // layout.tsx's Provider persists but page.tsx's rendered output
      // changes underneath it.
      act(() => {
        rerender(
          <BookingContactInfoProvider>
            <OtherChild />
            <ContactInfoReadout />
          </BookingContactInfoProvider>,
        );
      });

      expect(screen.getByText("a different child than before")).toBeInTheDocument();
      expect(screen.getByTestId("name")).toHaveTextContent("Alice");
    },
  );
});
