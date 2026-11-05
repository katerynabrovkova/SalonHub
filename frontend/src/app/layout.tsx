import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import { Suspense } from "react";
import "./globals.css";

import AssistantWidgetLoader from "./AssistantWidgetLoader";
import { AuthProvider } from "./AuthContext";
import NavigationProgress from "./NavigationProgress";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "SalonHub",
  description: "Платформа онлайн-запису для салонів краси.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html
      lang="uk"
      className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}
    >
      <body className="min-h-full flex flex-col">
        <Suspense fallback={null}>
          <NavigationProgress />
        </Suspense>
        <AuthProvider>{children}</AuthProvider>
        <Suspense fallback={null}>
          <AssistantWidgetLoader />
        </Suspense>
      </body>
    </html>
  );
}
