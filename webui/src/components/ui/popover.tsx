// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import * as React from "react";
import * as PopoverPrimitive from "@radix-ui/react-popover";

import { cn } from "@/lib/utils";
import { mergeRefs, useCssZoomFloatingLayer } from "./use-css-zoom-floating-layer";

const Popover = PopoverPrimitive.Root;
const PopoverTrigger = PopoverPrimitive.Trigger;
const PopoverAnchor = PopoverPrimitive.Anchor;

type PopoverContentProps = React.ComponentPropsWithoutRef<typeof PopoverPrimitive.Content> & {
  container?: HTMLElement | null;
};

const PopoverContent = React.forwardRef<
  React.ElementRef<typeof PopoverPrimitive.Content>,
  PopoverContentProps
>(({ className, align = "start", sideOffset = 6, container, ...props }, ref) => {
  const layerRef = React.useRef<React.ElementRef<typeof PopoverPrimitive.Content>>(null);
  useCssZoomFloatingLayer(layerRef);
  return (
    <PopoverPrimitive.Portal container={container}>
      <PopoverPrimitive.Content
        ref={mergeRefs(ref, layerRef)}
        align={align}
        sideOffset={sideOffset}
        className={cn(
          "pointer-events-auto z-[200] w-[var(--radix-popover-trigger-width)] min-w-[16rem] overflow-hidden rounded-[18px] border border-border/65 bg-popover p-0 text-popover-foreground shadow-[0_18px_55px_rgba(15,23,42,0.18)] outline-none dark:border-white/10 dark:shadow-[0_22px_55px_rgba(0,0,0,0.45)]",
          className,
        )}
        {...props}
      />
    </PopoverPrimitive.Portal>
  );
});
PopoverContent.displayName = PopoverPrimitive.Content.displayName;

export { Popover, PopoverTrigger, PopoverContent, PopoverAnchor };
