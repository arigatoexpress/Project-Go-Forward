import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import MobileChatPrompt from '../components/MobileChatPrompt';
import { consumeChatEntry, recordChatSend } from '../chatAnalytics';

function mockViewport(matches) {
  window.matchMedia = vi.fn().mockImplementation((query) => ({
    matches,
    media: query,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
  }));
}

async function readBeaconPayloads() {
  const payloads = [];
  for (const [, blob] of navigator.sendBeacon.mock.calls) {
    payloads.push(JSON.parse(await blob.text()));
  }
  return payloads;
}

describe('MobileChatPrompt', () => {
  beforeEach(() => {
    sessionStorage.clear();
    window.history.replaceState({}, '', '/');
    mockViewport(true);
    vi.stubGlobal('navigator', { sendBeacon: vi.fn(() => true) });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
    sessionStorage.clear();
  });

  it('shows a large dismissible prompt on a phone storefront page', async () => {
    const onOpenChat = vi.fn();
    render(<MobileChatPrompt activePage="inventory" onOpenChat={onOpenChat} />);

    expect(screen.getByTestId('mobile-chat-prompt')).toBeInTheDocument();
    const open = screen.getByRole('button', { name: 'Chat with Tex' });
    expect(open.className).toContain('min-h-12');
    expect(open.className).toContain('w-full');

    const shown = await readBeaconPayloads();
    expect(shown.map((item) => item.event)).toEqual(['chat_prompt_shown']);
    expect(shown[0].message).toBeUndefined();
    expect(shown[0].viewport).toBe('phone');
    expect(shown[0].page_path).toBe('/');

    fireEvent.click(screen.getByRole('button', { name: 'Dismiss chat prompt' }));
    expect(screen.queryByTestId('mobile-chat-prompt')).not.toBeInTheDocument();
    const launcher = screen.getByTestId('mobile-chat-launcher');
    expect(launcher.className).toContain('min-h-14');

    fireEvent.click(launcher);
    expect(onOpenChat).toHaveBeenCalledTimes(1);
    expect(consumeChatEntry()).toBe('mobile_launcher');

    const events = (await readBeaconPayloads()).map((item) => item.event);
    expect(events).toEqual(['chat_prompt_shown', 'chat_prompt_dismissed']);
  });

  it('does not render the phone prompt on a wide viewport', () => {
    mockViewport(false);
    render(<MobileChatPrompt activePage="inventory" onOpenChat={() => {}} />);
    expect(screen.queryByTestId('mobile-chat-prompt')).not.toBeInTheDocument();
    expect(screen.queryByTestId('mobile-chat-launcher')).not.toBeInTheDocument();
    expect(navigator.sendBeacon).not.toHaveBeenCalled();
  });

  it('does not render on staff pages or inside the chat itself', () => {
    const { rerender } = render(<MobileChatPrompt activePage="crm" onOpenChat={() => {}} />);
    expect(screen.queryByTestId('mobile-chat-prompt')).not.toBeInTheDocument();
    rerender(<MobileChatPrompt activePage="chat" onOpenChat={() => {}} />);
    expect(screen.queryByTestId('mobile-chat-prompt')).not.toBeInTheDocument();
    expect(navigator.sendBeacon).not.toHaveBeenCalled();
  });

  it('records a chat start and a message length without the message body', async () => {
    const text = 'My email is buyer@example.com and my phone is 2813243020';
    recordChatSend({ kind: 'typed', text });
    recordChatSend({ kind: 'typed', text: 'Still looking' });

    const payloads = await readBeaconPayloads();
    expect(payloads.map((item) => item.event)).toEqual([
      'chat_started',
      'chat_message_sent',
      'chat_message_sent',
    ]);
    const blob = JSON.stringify(payloads);
    expect(blob).not.toContain('buyer@example.com');
    expect(blob).not.toContain('2813243020');
    expect(payloads[1].message_length).toBe(text.length);
    expect(payloads[0].source).toBe('typed');
  });
});
