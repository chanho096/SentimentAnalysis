# BERT 기반 Aspect-based Sentiment Analysis를 이용한 영화 리뷰 분석 시스템

사전 학습된 KO-BERT (https://github.com/SKTBrain/KoBERT) 모델을 활용한 감성 분석 시스템 입니다.
탐색하고자 하는 주요 단어들을 지정하고, 지정된 단어에 대한 감성 분석을 실행할 수 있습니다.


### 프로그램 설치
requirements.txt 파일에 라이브러리 목록이 저장되어 있습니다.

windows 환경에서 pytorch 설치 시, 다음 사이트를 참조 바랍니다.
https://pytorch.org/get-started/locally/

```
pip install -r requirements.txt
```

## 모델 파일 다운로드
실행 경로에 ABSA_model.pt 파일이 존재해야 합니다.


## 모듈 설명
example.py - 모델 학습, 데이터 생성 등에 대한 예시 소스 코드 입니다.
prototype.py - 말뭉치 분석, 영화 리뷰 분석 등의 프로그램이 작성되어 있습니다.
model.py - ABSA Model 인터페이스 클래스가 구현되어 있습니다.
loader.py - Naver sentiment movie corpus 데이터를 불러옵니다. ( https://github.com/e9t/nsmc )

