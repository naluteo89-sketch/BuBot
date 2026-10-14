// 지난 질문 페이지: 서버가 만든 화면이라 사이드바의 대화 제목·질문 수·준비 상태만 다른 페이지와 같게 맞춥니다.
import './components/sidebar.js';
import {loadStatus} from './components/status.js';
import {showChatCount} from './components/chat-store.js';

showChatCount();
loadStatus();
