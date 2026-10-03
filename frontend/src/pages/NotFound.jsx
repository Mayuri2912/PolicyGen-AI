import { Link } from 'react-router-dom';
import { EmptyState } from '../components/ui.jsx';

export default function NotFound() {
  return (
    <EmptyState title="Page not found" action={<Link to="/" className="btn btn-primary">Go to the dashboard</Link>}>
      The page you asked for does not exist.
    </EmptyState>
  );
}
