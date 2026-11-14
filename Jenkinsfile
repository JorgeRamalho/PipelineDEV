pipeline {
    agent any

    options {
        timestamps()
        timeout(time: 20, unit: 'MINUTES')
        buildDiscarder(logRotator(numToKeepStr: '10'))
    }

    parameters {
        choice(
            name: 'RELEASE_MODE',
            choices: ['success', 'failure'],
            description: 'success conclui o release gate. failure força TESTS_PASSED=false para registrar FAILURE.'
        )
    }

    environment {
        IMAGE_NAME = 'python-devops-pipeline'
        IMAGE_TAG = "${BUILD_NUMBER}"
        TESTS_PASSED = "${params.RELEASE_MODE == 'failure' ? 'false' : 'true'}"
    }

    stages {
        stage('Checkout') {
            steps {
                checkout scm
                sh 'echo "Codigo obtido em $(pwd)" && find . -maxdepth 2 -type f | sort'
            }
        }

        stage('Instalar dependências') {
            steps {
                sh '''
                    set -eo pipefail
                    python3 -m venv .venv
                    . .venv/bin/activate
                    python -m pip install --upgrade pip
                    pip install -r requirements.txt
                '''
            }
        }

        stage('Testes Python / Pytest') {
            steps {
                sh '''
                    set -eo pipefail
                    . .venv/bin/activate
                    mkdir -p reports
                    pytest -v --junitxml=reports/pytest.xml
                '''
            }
        }

        stage('Build Docker') {
            steps {
                sh '''
                    set -eo pipefail
                    docker build -t "$IMAGE_NAME:$IMAGE_TAG" -t "$IMAGE_NAME:latest" .
                '''
            }
        }

        stage('Executar container') {
            steps {
                sh '''
                    set -eo pipefail
                    mkdir -p reports
                    docker run --rm \
                        -e TESTS_PASSED="$TESTS_PASSED" \
                        "$IMAGE_NAME:$IMAGE_TAG" | tee reports/container-result.json
                '''
            }
        }
    }

    post {
        always {
            archiveArtifacts artifacts: 'reports/**', allowEmptyArchive: true
        }
        success {
            echo 'RESULTADO DO PIPELINE: SUCCESS'
        }
        failure {
            echo 'RESULTADO DO PIPELINE: FAILURE'
        }
    }
}
