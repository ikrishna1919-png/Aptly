"use client";

import * as React from "react";
import * as DialogPrimitive from "@radix-ui/react-dialog";
import { X } from "lucide-react";

import { cn } from "@/lib/utils";

/**
 * shadcn/ui Dialog — thin styled wrappers over `@radix-ui/react-dialog`.
 *
 * Radix gives us the accessibility + behaviour we don't want to
 * re-implement: focus trap, scroll lock, `Escape` to close, click-
 * outside, and the correct `role="dialog"` / `aria-modal` wiring.
 *
 * The `LoginModal` consumes the lower-level primitives re-exported
 * here (Portal / Overlay / Content via `asChild`) so it can drive the
 * enter/exit with Framer Motion while keeping Radix's a11y intact.
 */

const Dialog = DialogPrimitive.Root;
const DialogTrigger = DialogPrimitive.Trigger;
const DialogPortal = DialogPrimitive.Portal;
const DialogClose = DialogPrimitive.Close;
const DialogOverlay = DialogPrimitive.Overlay;
const DialogContentPrimitive = DialogPrimitive.Content;

const DialogTitle = React.forwardRef<
  React.ElementRef<typeof DialogPrimitive.Title>,
  React.ComponentPropsWithoutRef<typeof DialogPrimitive.Title>
>(({ className, ...props }, ref) => (
  <DialogPrimitive.Title
    ref={ref}
    className={cn(
      "font-display text-2xl font-medium leading-none tracking-tight text-foreground",
      className,
    )}
    {...props}
  />
));
DialogTitle.displayName = DialogPrimitive.Title.displayName;

const DialogDescription = React.forwardRef<
  React.ElementRef<typeof DialogPrimitive.Description>,
  React.ComponentPropsWithoutRef<typeof DialogPrimitive.Description>
>(({ className, ...props }, ref) => (
  <DialogPrimitive.Description
    ref={ref}
    className={cn("text-sm leading-relaxed text-muted-foreground", className)}
    {...props}
  />
));
DialogDescription.displayName = DialogPrimitive.Description.displayName;

export {
  Dialog,
  DialogTrigger,
  DialogPortal,
  DialogClose,
  DialogOverlay,
  DialogContentPrimitive,
  DialogTitle,
  DialogDescription,
  // Re-export the close glyph so consumers don't re-import lucide.
  X as DialogCloseIcon,
};
