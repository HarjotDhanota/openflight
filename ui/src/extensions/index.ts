import type { ComponentType } from 'react';
import type { Socket } from 'socket.io-client';
import type { Shot } from '../types/shot';

/** What an extension sees of the live result: the shot shown and whether anything covers it. */
export interface LiveResultProps {
  shot: Shot | null;
  visible: boolean;
}

/**
 * An optional add-on to the kiosk UI. Each module in this folder default-exports one;
 * a build without any is the plain kiosk.
 */
export interface UiExtension {
  /** Drawn beside the ready light. */
  StatusControls?: ComponentType;
  /** Drawn among the live view's header actions. */
  LiveActions?: ComponentType;
  /** Rendered inside the live result (renders nothing on its own). */
  LiveResult?: ComponentType<LiveResultProps>;
  /** Once, when the socket's listeners are set up. */
  onSocket?(socket: Socket): void;
  /** On every (re)connect. */
  onConnect?(socket: Socket): void;
  /** A shot or a shot update arrived, before it is handled; must not throw. */
  onShotMessage?(event: 'shot' | 'shot_update', message: unknown): void;
  /** Whether the live view is on screen. */
  onLiveViewShown?(shown: boolean): void;
}

const modules = import.meta.glob<{ default: UiExtension }>(['./*.{ts,tsx}', '!./index.ts', '!./*.test.{ts,tsx}'], {
  eager: true,
});

export const uiExtensions: readonly UiExtension[] = Object.keys(modules)
  .sort()
  .map((path) => modules[path].default);
