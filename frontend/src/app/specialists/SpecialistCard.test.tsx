// @vitest-environment jsdom
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import SpecialistCard, { type SpecialistCardData } from "./SpecialistCard";

function specialist(overrides: Partial<SpecialistCardData> = {}): SpecialistCardData {
  return {
    name: "Olena",
    bio: "Nail artist",
    photo: "https://example.com/photos/olena.jpg",
    average_rating: 4.5,
    review_count: 2,
    services: [{ id: 10, name: "Manicure" }],
    ...overrides,
  };
}

describe("SpecialistCard", () => {
  it("test_renders_photo_when_present", () => {
    render(<SpecialistCard specialist={specialist()} variant="grid" />);

    expect(screen.getByRole("img", { name: "Olena" })).toBeInTheDocument();
  });

  it("test_renders_initials_fallback_when_no_photo", () => {
    render(<SpecialistCard specialist={specialist({ photo: null })} variant="grid" />);

    expect(screen.queryByRole("img")).not.toBeInTheDocument();
    expect(screen.getByText("O")).toBeInTheDocument();
  });

  it("test_renders_bio_when_present", () => {
    render(<SpecialistCard specialist={specialist({ bio: "Nail artist" })} variant="grid" />);

    expect(screen.getByText("Nail artist")).toBeInTheDocument();
  });

  it("test_omits_bio_when_empty", () => {
    render(<SpecialistCard specialist={specialist({ bio: "" })} variant="grid" />);

    expect(screen.queryByText("Nail artist")).not.toBeInTheDocument();
  });

  it("test_rating_line_with_reviews", () => {
    render(
      <SpecialistCard
        specialist={specialist({ average_rating: 4.5, review_count: 2 })}
        variant="grid"
      />,
    );

    expect(screen.getByText("★ 4.5 (2 відгуків)")).toBeInTheDocument();
  });

  it("test_rating_line_without_reviews", () => {
    render(
      <SpecialistCard
        specialist={specialist({ average_rating: null, review_count: 0 })}
        variant="grid"
      />,
    );

    expect(screen.getByText("Немає відгуків")).toBeInTheDocument();
  });

  it("test_grid_variant_renders_services_but_detail_variant_does_not", () => {
    const data = specialist({ services: [{ id: 10, name: "Manicure" }] });

    const { unmount } = render(<SpecialistCard specialist={data} variant="grid" />);
    expect(screen.getByText("Manicure")).toBeInTheDocument();
    unmount();

    render(<SpecialistCard specialist={data} variant="detail" />);
    expect(screen.queryByText("Manicure")).not.toBeInTheDocument();
  });
});
