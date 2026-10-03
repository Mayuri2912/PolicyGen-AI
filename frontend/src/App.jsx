import { Route, Routes } from 'react-router-dom';
import AppLayout from './layouts/AppLayout.jsx';
import AnalysisPage from './pages/AnalysisPage.jsx';
import ConfigurePage from './pages/ConfigurePage.jsx';
import Dashboard from './pages/Dashboard.jsx';
import HelpPage from './pages/HelpPage.jsx';
import NotFound from './pages/NotFound.jsx';
import ReportPage from './pages/ReportPage.jsx';
import ReportsPage from './pages/ReportsPage.jsx';
import UploadPage from './pages/UploadPage.jsx';

export default function App() {
  return (
    <Routes>
      <Route element={<AppLayout />}>
        <Route index element={<Dashboard />} />
        <Route path="new" element={<UploadPage />} />
        <Route path="analyses/:analysisId" element={<AnalysisPage />} />
        <Route path="analyses/:analysisId/configure" element={<ConfigurePage />} />
        <Route path="reports" element={<ReportsPage />} />
        <Route path="reports/:reportId" element={<ReportPage />} />
        <Route path="help" element={<HelpPage />} />
        <Route path="*" element={<NotFound />} />
      </Route>
    </Routes>
  );
}
