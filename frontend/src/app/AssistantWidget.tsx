"use client";

/**
 * Assistant widget placeholder (docs/DECISIONS.md § "Stage 17 sub-step
 * order", 17.3b). Renders nothing for now; 17.3c builds the stripe, window
 * and side panel here.
 */

export interface AssistantWidgetProps {
  salonName: string;
}

// eslint-disable-next-line @typescript-eslint/no-unused-vars -- placeholder: 17.3c uses salonName for the header
export default function AssistantWidget(props: AssistantWidgetProps) {
  return null;
}
