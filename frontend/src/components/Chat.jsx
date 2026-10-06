export default function Chat({ role }) {
  return (
    <section aria-label="Agent chat">
      <h2>Agent Chat</h2>
      <p>Chat for {role} will appear here.</p>
      <label htmlFor={`${role}-message`}>Message</label>
      <input id={`${role}-message`} type="text" disabled placeholder="AI integration coming soon" />
      <button type="button" disabled>Send</button>
      {/* TODO: Connect this component to the FastAPI chat endpoint. */}
    </section>
  );
}
