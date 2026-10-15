import api from '../api';
import { ErrorNotice, useResource } from '../ui';
import ChatEvaluation from './ChatEvaluation';

export default function EvaluationPage({ session, active, navigate }) {
  const catalog = useResource(() => active ? api.chatModels() : Promise.resolve(null), [active]);
  return <div className="evaluation-page">
    <ErrorNotice error={catalog.error} retry={catalog.reload} />
    <ChatEvaluation key={session.session_id} catalog={catalog.data} onModelsChanged={catalog.reload} active={active} sessionId={session.session_id} />
  </div>;
}
