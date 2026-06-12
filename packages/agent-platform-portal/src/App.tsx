import { Navigate, Route, Routes } from "react-router-dom";
import Layout from "./components/Layout";
import MyAgents from "./pages/MyAgents";
import MyUsage from "./pages/MyUsage";
import RequestAgent from "./pages/RequestAgent";
import Requests from "./pages/Requests";
import Terminal from "./pages/Terminal";

export default function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route path="/" element={<Navigate to="/my-agents" replace />} />
        <Route path="/my-agents" element={<MyAgents />} />
        <Route path="/request" element={<RequestAgent />} />
        <Route path="/requests" element={<Requests />} />
        <Route path="/usage" element={<MyUsage />} />
        <Route path="/terminal/:vmId" element={<Terminal />} />
        <Route path="*" element={<div className="p-8">Not found</div>} />
      </Route>
    </Routes>
  );
}
