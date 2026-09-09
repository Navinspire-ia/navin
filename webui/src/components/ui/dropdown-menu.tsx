// Copyright (c) 2026-present Navinspire IA
// SPDX-License-Identifier: AGPL-3.0-only

import * as React from "react";
import * as DropdownMenuPrimitive from "@radix-ui/react-dropdown-menu";
import { Check, ChevronRight, Circle } from "lucide-react";

import { bindMenuListWheel } from "@/lib/model-picker-scroll";
import { cn } from "@/lib/utils";
import { mergeRefs, useCssZoomFloatingLayer } from "./use-css-zoom-floating-layer";

/**
 * Non-modal by default. WKWebView (macOS) and WebKitGTK mis-hit the
 * Radix dismiss layer when `modal` is on, so a click on a composer
 * row lands on another item (often "Manage models"). ChatList and the
 * session menu already opted out; the rest of the app follows.
 */
function DropdownMenu({
  modal = false,
  ...props
}: React.ComponentProps<typeof DropdownMenuPrimitive.Root>) {
  return <DropdownMenuPrimitive.Root modal={modal} {...props} />;
}
const DropdownMenuTrigger = DropdownMenuPrimitive.Trigger;
const DropdownMenuGroup = DropdownMenuPrimitive.Group;
const DropdownMenuPortal = DropdownMenuPrimitive.Portal;
const DropdownMenuSub = DropdownMenuPrimitive.Sub;
const DropdownMenuRadioGroup = DropdownMenuPrimitive.RadioGroup;

const menuContentClassName =
  // Above Dialog overlay/content (z-50) so pickers inside modals stay clickable.
  // Opaque on purpose: WKWebView (macOS, Intel especially) cannot composite
  // backdrop-filter plus a 96% panel, so the file tree shows through and the
  // hover bar ghosts onto the next row. Chromium (Windows, localhost) hid it.
  // min-h-0: Radix wraps content in a flex popper; without it WebKit will not
  // shrink the menu below its children, so overflow-y-auto never scrolls.
  // The CSS variable often arrives empty on WebKitGTK / CSS zoom, which makes
  // the whole min() invalid and the menu grow past the window with no scroll.
  // A 70dvh / 5.5rem-from-viewport fallback keeps a real scrollport.
  "z-[200] max-h-[min(var(--radix-dropdown-menu-content-available-height,70dvh),min(28rem,calc(100dvh-5.5rem)))] min-h-0 min-w-[10rem] overflow-x-hidden overflow-y-auto overscroll-contain rounded-[18px] border border-border/65 bg-popover p-1.5 text-popover-foreground shadow-[0_18px_55px_rgba(15,23,42,0.18)] scrollbar-thin scrollbar-track-transparent dark:border-white/10 dark:shadow-[0_22px_55px_rgba(0,0,0,0.45)]";

const menuItemClassName =
  "relative flex min-h-8 cursor-default select-none items-center gap-2 rounded-[12px] px-2.5 py-2 text-[13px] outline-none transition-colors focus:bg-foreground/[0.055] focus:text-foreground data-[disabled]:pointer-events-none data-[disabled]:opacity-50 dark:focus:bg-white/[0.08]";

const DropdownMenuSubTrigger = React.forwardRef<
  React.ElementRef<typeof DropdownMenuPrimitive.SubTrigger>,
  React.ComponentPropsWithoutRef<typeof DropdownMenuPrimitive.SubTrigger> & {
    inset?: boolean;
  }
>(({ className, inset, children, ...props }, ref) => (
  <DropdownMenuPrimitive.SubTrigger
    ref={ref}
    className={cn(
      menuItemClassName,
      "data-[state=open]:bg-foreground/[0.055] dark:data-[state=open]:bg-white/[0.08]",
      inset && "pl-8",
      className,
    )}
    {...props}
  >
    {children}
    <ChevronRight className="ml-auto h-3.5 w-3.5 text-muted-foreground" />
  </DropdownMenuPrimitive.SubTrigger>
));
DropdownMenuSubTrigger.displayName = DropdownMenuPrimitive.SubTrigger.displayName;

const DropdownMenuSubContent = React.forwardRef<
  React.ElementRef<typeof DropdownMenuPrimitive.SubContent>,
  React.ComponentPropsWithoutRef<typeof DropdownMenuPrimitive.SubContent>
>(({ className, ...props }, ref) => {
  const layerRef = React.useRef<React.ElementRef<typeof DropdownMenuPrimitive.SubContent>>(null);
  useCssZoomFloatingLayer(layerRef);
  React.useLayoutEffect(() => {
    const node = layerRef.current;
    return node ? bindMenuListWheel(node) : undefined;
  });
  return (
    <DropdownMenuPrimitive.SubContent
      ref={mergeRefs(ref, layerRef)}
      data-menu-scroll=""
      className={cn(
        menuContentClassName,
        "navin-menu-presence",
        className,
      )}
      {...props}
    />
  );
});
DropdownMenuSubContent.displayName = DropdownMenuPrimitive.SubContent.displayName;

interface DropdownMenuContentProps
  extends React.ComponentPropsWithoutRef<typeof DropdownMenuPrimitive.Content> {
  portalContainer?: HTMLElement | null;
}

const DropdownMenuContent = React.forwardRef<
  React.ElementRef<typeof DropdownMenuPrimitive.Content>,
  DropdownMenuContentProps
>(({ className, sideOffset = 4, portalContainer, onCloseAutoFocus, style, ...props }, ref) => {
  const layerRef = React.useRef<React.ElementRef<typeof DropdownMenuPrimitive.Content>>(null);
  useCssZoomFloatingLayer(layerRef);
  React.useLayoutEffect(() => {
    const node = layerRef.current;
    return node ? bindMenuListWheel(node) : undefined;
  });
  return (
    <DropdownMenuPrimitive.Portal container={portalContainer ?? undefined}>
      <DropdownMenuPrimitive.Content
        ref={mergeRefs(ref, layerRef)}
        data-menu-scroll=""
        sideOffset={sideOffset}
        collisionPadding={8}
        style={{ minHeight: 0, ...style }}
        onCloseAutoFocus={(event) => {
          // Returning focus to the trigger remounts hover styles on WebKit
          // and makes the Effort / model chips flicker after a pick.
          event.preventDefault();
          onCloseAutoFocus?.(event);
        }}
        className={cn(
          menuContentClassName,
          // Opacity-only close. tailwindcss-animate's `animate-out` writes
          // `transform: translate3d(0,0,0)` on the content; WebKitGTK flattens
          // that against the popper wrapper so the menu blinks at the origin
          // on outside-click instead of fading in place.
          "navin-menu-presence",
          className,
        )}
        {...props}
      />
    </DropdownMenuPrimitive.Portal>
  );
});
DropdownMenuContent.displayName = DropdownMenuPrimitive.Content.displayName;

const DropdownMenuItem = React.forwardRef<
  React.ElementRef<typeof DropdownMenuPrimitive.Item>,
  React.ComponentPropsWithoutRef<typeof DropdownMenuPrimitive.Item> & {
    inset?: boolean;
  }
>(({ className, inset, ...props }, ref) => (
  <DropdownMenuPrimitive.Item
    ref={ref}
    className={cn(
      menuItemClassName,
      inset && "pl-8",
      className,
    )}
    {...props}
  />
));
DropdownMenuItem.displayName = DropdownMenuPrimitive.Item.displayName;

const DropdownMenuCheckboxItem = React.forwardRef<
  React.ElementRef<typeof DropdownMenuPrimitive.CheckboxItem>,
  React.ComponentPropsWithoutRef<typeof DropdownMenuPrimitive.CheckboxItem>
>(({ className, children, checked, ...props }, ref) => (
  <DropdownMenuPrimitive.CheckboxItem
    ref={ref}
    className={cn(
      menuItemClassName,
      "pl-8 pr-2.5",
      className,
    )}
    checked={checked}
    {...props}
  >
    <span className="absolute left-2.5 flex h-3.5 w-3.5 items-center justify-center">
      <DropdownMenuPrimitive.ItemIndicator>
        <Check className="h-3.5 w-3.5" />
      </DropdownMenuPrimitive.ItemIndicator>
    </span>
    {children}
  </DropdownMenuPrimitive.CheckboxItem>
));
DropdownMenuCheckboxItem.displayName =
  DropdownMenuPrimitive.CheckboxItem.displayName;

const DropdownMenuRadioItem = React.forwardRef<
  React.ElementRef<typeof DropdownMenuPrimitive.RadioItem>,
  React.ComponentPropsWithoutRef<typeof DropdownMenuPrimitive.RadioItem>
>(({ className, children, ...props }, ref) => (
  <DropdownMenuPrimitive.RadioItem
    ref={ref}
    className={cn(
      menuItemClassName,
      "pl-8 pr-2.5",
      className,
    )}
    {...props}
  >
    <span className="absolute left-2.5 flex h-3.5 w-3.5 items-center justify-center">
      <DropdownMenuPrimitive.ItemIndicator>
        <Circle className="h-2 w-2 fill-current" />
      </DropdownMenuPrimitive.ItemIndicator>
    </span>
    {children}
  </DropdownMenuPrimitive.RadioItem>
));
DropdownMenuRadioItem.displayName = DropdownMenuPrimitive.RadioItem.displayName;

const DropdownMenuLabel = React.forwardRef<
  React.ElementRef<typeof DropdownMenuPrimitive.Label>,
  React.ComponentPropsWithoutRef<typeof DropdownMenuPrimitive.Label> & {
    inset?: boolean;
  }
>(({ className, inset, ...props }, ref) => (
  <DropdownMenuPrimitive.Label
    ref={ref}
    className={cn(
      "px-2.5 pb-1.5 pt-1 text-[12px] font-semibold text-muted-foreground",
      inset && "pl-8",
      className,
    )}
    {...props}
  />
));
DropdownMenuLabel.displayName = DropdownMenuPrimitive.Label.displayName;

const DropdownMenuSeparator = React.forwardRef<
  React.ElementRef<typeof DropdownMenuPrimitive.Separator>,
  React.ComponentPropsWithoutRef<typeof DropdownMenuPrimitive.Separator>
>(({ className, ...props }, ref) => (
  <DropdownMenuPrimitive.Separator
    ref={ref}
    className={cn("-mx-1.5 my-1.5 h-px bg-border/50", className)}
    {...props}
  />
));
DropdownMenuSeparator.displayName = DropdownMenuPrimitive.Separator.displayName;

export {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuPortal,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
  DropdownMenuTrigger,
};
